using Azure;
using Azure.AI.OpenAI;
using AzureOpenAICodeGen.Models;
using OpenAI.Chat;
using System.Linq;
using System.ClientModel;

namespace AzureOpenAICodeGen.Services;

internal sealed class AzureOpenAIService
{
    private readonly ChatClient _chatClient;

    internal sealed record CompatibilityRetryStats(int Attempts, bool DroppedMaxTokens, bool DroppedTemperature);

    internal sealed record GenerationResult(string Text, CompatibilityRetryStats RetryStats);

    public AzureOpenAIService(ChatClient chatClient)
    {
        _chatClient = chatClient ?? throw new ArgumentNullException(nameof(chatClient));
    }

    public async Task<string> GenerateCodeAsync(string prompt, GenerationOptions options, CancellationToken cancellationToken)
        => (await GenerateCodeWithStatsAsync(prompt, options, cancellationToken).ConfigureAwait(false)).Text;

    public async Task<GenerationResult> GenerateCodeWithStatsAsync(string prompt, GenerationOptions options, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(prompt))
        {
            throw new ArgumentException("Prompt cannot be empty.", nameof(prompt));
        }

        var messages = new List<ChatMessage>
        {
            new SystemChatMessage(string.IsNullOrWhiteSpace(options.SystemPrompt)
                ? "You are an expert assistant that returns only executable code unless explicitly told otherwise."
                : options.SystemPrompt),
            new UserChatMessage(prompt)
        };

        var requestOptions = new ChatCompletionOptions
        {
            Temperature = (float?)options.Temperature,
            ResponseFormat = ChatResponseFormat.CreateTextFormat()
        };

        if (options.MaxOutputTokens > 0)
        {
            requestOptions.MaxOutputTokenCount = options.MaxOutputTokens;
        }

        try
        {
            cancellationToken.ThrowIfCancellationRequested();

            var (response, retryStats) = await CompleteWithCompatibilityRetryAsync(messages, requestOptions, cancellationToken).ConfigureAwait(false);
            var completion = response.Value;
            if (completion.Content.Count == 0)
            {
                throw new InvalidOperationException("Azure OpenAI returned an empty response.");
            }

            var text = string.Concat(completion.Content.Select(part => part.Text)).Trim();
            if (string.IsNullOrWhiteSpace(text))
            {
                throw new InvalidOperationException("Azure OpenAI returned an empty response.");
            }

            return new GenerationResult(text, retryStats);
        }
        catch (ClientResultException ex)
        {
            throw new InvalidOperationException("Azure OpenAI request failed. See inner exception for details.", ex);
        }
        catch (RequestFailedException ex)
        {
            throw new InvalidOperationException("Azure OpenAI request failed. See inner exception for details.", ex);
        }
    }

    private async Task<(ClientResult<ChatCompletion> Result, CompatibilityRetryStats Stats)> CompleteWithCompatibilityRetryAsync(
        IEnumerable<ChatMessage> messages,
        ChatCompletionOptions requestOptions,
        CancellationToken cancellationToken)
    {
        var currentOptions = requestOptions;

        var attempts = 0;
        var droppedMaxTokens = false;
        var droppedTemperature = false;

        for (var attempt = 0; attempt < 3; attempt++)
        {
            try
            {
                attempts++;
                var result = await _chatClient.CompleteChatAsync(messages, currentOptions, cancellationToken).ConfigureAwait(false);
                return (result, new CompatibilityRetryStats(attempts, droppedMaxTokens, droppedTemperature));
            }
            catch (Exception ex) when (ex is ClientResultException || ex is RequestFailedException)
            {
                var (nextOptions, delta) = BuildCompatibilityRetryOptions(currentOptions, ex.Message ?? string.Empty);
                if (nextOptions is null)
                {
                    throw;
                }

                droppedMaxTokens |= delta.DroppedMaxTokens;
                droppedTemperature |= delta.DroppedTemperature;
                currentOptions = nextOptions;
            }
        }

        // Should not be reachable.
        attempts++;
        var last = await _chatClient.CompleteChatAsync(messages, currentOptions, cancellationToken).ConfigureAwait(false);
        return (last, new CompatibilityRetryStats(attempts, droppedMaxTokens, droppedTemperature));
    }

    private static (ChatCompletionOptions? Options, CompatibilityRetryStats Delta) BuildCompatibilityRetryOptions(
        ChatCompletionOptions original,
        string message)
    {
        // Some deployments (including GPT-5 in certain Azure configurations) reject legacy parameters like
        // `max_tokens` and/or `temperature`.
        var dropMaxTokens = IsUnsupportedParameter(message, "max_tokens");
        var dropTemperature = IsUnsupportedParameter(message, "temperature");

        if (!dropMaxTokens && !dropTemperature)
        {
            return (null, new CompatibilityRetryStats(0, false, false));
        }

        var retry = new ChatCompletionOptions
        {
            ResponseFormat = original.ResponseFormat
        };

        if (!dropTemperature)
        {
            retry.Temperature = original.Temperature;
        }

        if (!dropMaxTokens)
        {
            retry.MaxOutputTokenCount = original.MaxOutputTokenCount;
        }

        return (retry, new CompatibilityRetryStats(0, dropMaxTokens, dropTemperature));
    }

    private static bool IsUnsupportedParameter(string message, string param)
    {
        if (string.IsNullOrWhiteSpace(message))
        {
            return false;
        }

        return message.Contains($"Unsupported parameter: '{param}'", StringComparison.OrdinalIgnoreCase)
            || message.Contains($"Parameter: {param}", StringComparison.OrdinalIgnoreCase);
    }
}
