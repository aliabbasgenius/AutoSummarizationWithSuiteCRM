using System.Text.Json;

namespace AzureOpenAICodeGen.Services;

internal static class RunStatsLogger
{
    private static readonly JsonSerializerOptions SerializerOptions = new()
    {
        WriteIndented = false
    };

    public static string GetDefaultLogPath()
    {
        var root = FindRepoRoot();
        if (root is null)
        {
            // Fall back to current working directory if the repo root can't be located.
            return Path.GetFullPath(Path.Combine(Directory.GetCurrentDirectory(), "runs", "azure_openai_runs.jsonl"));
        }

        return Path.Combine(root, "LLMCodeGenerator", "AzureOpenAICodeGen", "runs", "azure_openai_runs.jsonl");
    }

    public static void Append(string logPath, object payload)
    {
        var directory = Path.GetDirectoryName(logPath);
        if (!string.IsNullOrWhiteSpace(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var line = JsonSerializer.Serialize(payload, SerializerOptions);
        File.AppendAllText(logPath, line + Environment.NewLine);
    }

    private static string? FindRepoRoot()
    {
        // Try current working directory first.
        var cwd = new DirectoryInfo(Directory.GetCurrentDirectory());
        var fromCwd = FindRepoRootFrom(cwd);
        if (fromCwd is not null)
        {
            return fromCwd;
        }

        // Then try from the bin folder.
        var baseDir = new DirectoryInfo(AppContext.BaseDirectory);
        return FindRepoRootFrom(baseDir);
    }

    private static string? FindRepoRootFrom(DirectoryInfo start)
    {
        for (var current = start; current is not null; current = current.Parent)
        {
            try
            {
                // Anchor on the solution file for this repo.
                var sln = Path.Combine(current.FullName, "AutoSummarizationProject.sln");
                if (File.Exists(sln))
                {
                    return current.FullName;
                }
            }
            catch
            {
                // ignore and keep walking up
            }
        }

        return null;
    }
}
