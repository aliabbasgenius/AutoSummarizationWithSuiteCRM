using AzureOpenAICodeGen.Models;

namespace AzureOpenAICodeGen.Services;

internal static class PromptLoader
{
    public static async Task<string> LoadAsync(string relativePath, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(relativePath))
        {
            throw new ArgumentException("Prompt path must be provided.", nameof(relativePath));
        }

        var absolutePath = Path.GetFullPath(relativePath);
        if (!File.Exists(absolutePath))
        {
            throw new FileNotFoundException($"Prompt file not found at '{absolutePath}'.", absolutePath);
        }

        await using var promptStream = File.OpenRead(absolutePath);
        using var reader = new StreamReader(promptStream);
        return await reader.ReadToEndAsync(cancellationToken);
    }
}
