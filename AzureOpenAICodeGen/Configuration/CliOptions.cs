using AzureOpenAICodeGen.Models;
using Microsoft.Extensions.Configuration;

namespace AzureOpenAICodeGen.Configuration;

internal sealed record CliOptions(AzureOpenAISettings Settings, string? RefactorFile, bool ShowStats, string? StatsPath)
{
    public static CliOptions FromArgs(string[] args, IConfiguration configuration)
    {
        var settings = AzureOpenAISettings.FromConfiguration(configuration);
        string? refactorFile = null;
        var showStats = false;
        string? statsPath = null;

        for (var i = 0; i < args.Length; i++)
        {
            var arg = args[i];
            if (!arg.StartsWith("--", StringComparison.Ordinal))
            {
                continue;
            }

            // Support both --key=value and --key value formats.
            var segments = arg.Split('=', 2, StringSplitOptions.TrimEntries);
            var key = segments[0][2..];
            var value = segments.Length == 2 ? segments[1] : null;

            if (value is null && i + 1 < args.Length && !args[i + 1].StartsWith("--", StringComparison.Ordinal))
            {
                value = args[++i];
            }

            switch (key)
            {
                case "stats":
                    // Allow --stats, --stats=1, --stats true
                    showStats = value is null ? true : IsTruthy(value);
                    break;
                case "statsPath" when !string.IsNullOrWhiteSpace(value):
                    statsPath = value;
                    break;
                case "refactor" when !string.IsNullOrWhiteSpace(value):
                    refactorFile = value;
                    break;
                case "prompt" when !string.IsNullOrWhiteSpace(value):
                    settings.PromptPath = value;
                    break;
                case "output" when !string.IsNullOrWhiteSpace(value):
                    settings.OutputPath = value;
                    break;
                case "deployment" when !string.IsNullOrWhiteSpace(value):
                    settings.DeploymentName = value;
                    break;
                case "system" when !string.IsNullOrWhiteSpace(value):
                    settings.SystemPrompt = value;
                    break;
                case "temperature" when value is not null && double.TryParse(value, out var parsedTemperature):
                    settings.Temperature = parsedTemperature;
                    break;
                case "maxTokens" when value is not null && int.TryParse(value, out var parsedTokens):
                    settings.MaxOutputTokens = parsedTokens;
                    break;
            }
        }

        return new CliOptions(settings, refactorFile, showStats, statsPath);
    }

    private static bool IsTruthy(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return false;
        }

        return value.Equals("1", StringComparison.OrdinalIgnoreCase)
            || value.Equals("true", StringComparison.OrdinalIgnoreCase)
            || value.Equals("yes", StringComparison.OrdinalIgnoreCase)
            || value.Equals("y", StringComparison.OrdinalIgnoreCase);
    }
}
