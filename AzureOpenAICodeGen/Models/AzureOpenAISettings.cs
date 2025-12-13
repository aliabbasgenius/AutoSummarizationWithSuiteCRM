using Microsoft.Extensions.Configuration;

namespace AzureOpenAICodeGen.Models;

internal sealed class AzureOpenAISettings
{
    private const string DefaultSystemPrompt = "You are an expert software engineer who writes concise, production-ready code.";
    private const double DefaultTemperature = 0.2;
    private const int DefaultMaxOutputTokens = 1200;

    public string Endpoint { get; set; } = GetEnvironmentValue("AZURE_OPENAI_ENDPOINT");
    public string ApiKey { get; set; } = GetEnvironmentValue("AZURE_OPENAI_KEY", "AZURE_OPENAI_API_KEY");
    public string DeploymentName { get; set; } = GetEnvironmentValue("AZURE_OPENAI_DEPLOYMENT");
    public string PromptPath { get; set; } = "../prompt.txt";
    public string OutputPath { get; set; } = "./generated_code.txt";
    public string SystemPrompt { get; set; } = DefaultSystemPrompt;
    public double Temperature { get; set; } = DefaultTemperature;
    public int MaxOutputTokens { get; set; } = DefaultMaxOutputTokens;

    public static AzureOpenAISettings FromConfiguration(IConfiguration configuration)
    {
        var settings = new AzureOpenAISettings();
        configuration.GetSection("AzureOpenAI").Bind(settings);

        settings.Endpoint = NormalizeAzureEndpoint(ResolveString(configuration, "AZURE_OPENAI_ENDPOINT", settings.Endpoint));
        settings.ApiKey = ResolveString(configuration, "AZURE_OPENAI_KEY", settings.ApiKey);
        settings.DeploymentName = ResolveString(configuration, "AZURE_OPENAI_DEPLOYMENT", settings.DeploymentName);

        return settings;
    }

    private static string NormalizeAzureEndpoint(string raw)
    {
        if (string.IsNullOrWhiteSpace(raw))
        {
            return string.Empty;
        }

        raw = raw.Trim();

        // Users sometimes paste the full REST URL (e.g. .../openai/deployments/<deployment>/chat/completions?...)
        // The Azure SDK expects the base resource endpoint: https://<resource>.openai.azure.com/
        if (Uri.TryCreate(raw, UriKind.Absolute, out var uri)
            && uri.Host.EndsWith(".openai.azure.com", StringComparison.OrdinalIgnoreCase))
        {
            return $"{uri.Scheme}://{uri.Host}/";
        }

        // Handle host-only values like "my-resource.openai.azure.com".
        if (!raw.Contains("://", StringComparison.Ordinal) && raw.Contains(".openai.azure.com", StringComparison.OrdinalIgnoreCase))
        {
            var host = raw.Split('/', 2)[0];
            return $"https://{host}/";
        }

        return raw;
    }

    private static string ResolveString(IConfiguration configuration, string key, string currentValue)
    {
        var fromConfig = configuration[key];
        if (!string.IsNullOrWhiteSpace(fromConfig))
        {
            return fromConfig;
        }

        return string.IsNullOrWhiteSpace(currentValue) ? string.Empty : currentValue;
    }
    private static string GetEnvironmentValue(params string[] variableNames)
    {
        foreach (var name in variableNames)
        {
            var value = Environment.GetEnvironmentVariable(name);
            if (!string.IsNullOrWhiteSpace(value))
            {
                return value;
            }
        }

        return string.Empty;
    }
}
