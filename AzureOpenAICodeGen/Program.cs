using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;
using Azure;
using Azure.AI.OpenAI;
using AzureOpenAICodeGen.Configuration;
using AzureOpenAICodeGen.Models;
using AzureOpenAICodeGen.Services;
using DotNetEnv;
using Microsoft.Extensions.Configuration;

using var cancellationSource = new CancellationTokenSource();
Console.CancelKeyPress += (_, eventArgs) =>
{
    cancellationSource.Cancel();
    eventArgs.Cancel = true;
};

LoadDotEnv();

var configuration = BuildConfiguration(args);
var options = CliOptions.FromArgs(args, configuration);

if (options.ShowStats)
{
    var statsPath = !string.IsNullOrWhiteSpace(options.StatsPath)
        ? Path.GetFullPath(options.StatsPath)
        : RunStatsLogger.GetDefaultLogPath();

    var docs = RunStatsSummary.ReadJsonl(statsPath).ToList();
    RunStatsSummary.Print(statsPath, docs);
    return;
}

ValidateSettings(options.Settings);

var openAiClient = new AzureOpenAIClient(new Uri(options.Settings.Endpoint), new AzureKeyCredential(options.Settings.ApiKey));
var chatClient = openAiClient.GetChatClient(options.Settings.DeploymentName);
var generationService = new AzureOpenAIService(chatClient);

var generationOptions = new GenerationOptions
{
    SystemPrompt = options.Settings.SystemPrompt,
    Temperature = options.Settings.Temperature,
    MaxOutputTokens = options.Settings.MaxOutputTokens
};

var runLogPath = RunStatsLogger.GetDefaultLogPath();
var patchOutputDirectory = Path.Combine(Path.GetDirectoryName(runLogPath) ?? Directory.GetCurrentDirectory(), "patches");

if (!string.IsNullOrWhiteSpace(options.RefactorFile))
{
    var startedUtc = DateTimeOffset.UtcNow;
    var targetPath = ResolvePath(options.RefactorFile);
    if (!File.Exists(targetPath))
    {
        throw new InvalidOperationException($"Refactor target not found: {targetPath}");
    }

    var fileText = ReadAllTextBestEffort(targetPath);
    var displayPath = NormalizeForDiff(targetPath);
    var resolvedOutputPath = ResolveRefactorOutputPath(options.Settings.OutputPath, patchOutputDirectory);

    var refactorPrompt = $"""
You are refactoring a SuiteCRM PHP file.

Goals:
- Keep behavior identical (no functional changes).
- Improve readability/structure.
- Keep public interfaces stable.
- Do not change comments/docblocks unless strictly necessary.
- Do not change docblock types/annotations unless required for correctness.

Prohibited changes:
- Do NOT add parameter type hints or return type hints.
- Do NOT add typed properties.
- Do NOT change any function signature (name/visibility/parameters/defaults/return behavior).
- Do NOT change namespaces or class names.

Return ONLY a unified diff (git apply compatible) for exactly ONE file.
- The very first line MUST be: diff --git a/{displayPath} b/{displayPath}
- Include unified diff headers: --- a/{displayPath} and +++ b/{displayPath}
- Include at least one @@ hunk header.
- Minimal diff; no extra commentary.
- Do NOT wrap the diff in markdown fences like ``` or ```diff.
- Every line in each hunk must start with ' ', '+', or '-'. For blank context lines, use a single leading space.

Target file: {displayPath}

Current contents:
```php
{fileText}
```
""";

    var refactorStopwatch = Stopwatch.StartNew();
    try
    {
        var result = await generationService.GenerateCodeWithStatsAsync(refactorPrompt, generationOptions, cancellationSource.Token);
        var patchText = BuildGitApplyPatch(displayPath, targetPath, fileText, result.Text);
        await OutputWriter.WriteAsync(resolvedOutputPath, patchText, cancellationSource.Token);
        refactorStopwatch.Stop();

        RunStatsLogger.Append(
            runLogPath,
            new
            {
                timestamp_utc = startedUtc,
                mode = "refactor",
                deployment = options.Settings.DeploymentName,
                endpoint = new Uri(options.Settings.Endpoint).Host,
                target_file = targetPath,
                output_file = resolvedOutputPath,
                duration_seconds = Math.Round(refactorStopwatch.Elapsed.TotalSeconds, 3),
                retry = new
                {
                    attempts = result.RetryStats.Attempts,
                    dropped_max_tokens = result.RetryStats.DroppedMaxTokens,
                    dropped_temperature = result.RetryStats.DroppedTemperature,
                },
                output_chars = result.Text?.Length ?? 0,
                success = true,
            }
        );

        Console.WriteLine($"Refactor completed in {refactorStopwatch.Elapsed.TotalSeconds.ToString("F2", CultureInfo.InvariantCulture)} seconds.");
        Console.WriteLine($"Unified diff written to: {resolvedOutputPath}");
        Console.WriteLine($"Run stats appended to: {runLogPath}");
        return;
    }
    catch (Exception ex)
    {
        refactorStopwatch.Stop();
        try
        {
            RunStatsLogger.Append(
                runLogPath,
                new
                {
                    timestamp_utc = startedUtc,
                    mode = "refactor",
                    deployment = options.Settings.DeploymentName,
                    endpoint = new Uri(options.Settings.Endpoint).Host,
                    target_file = targetPath,
                    output_file = resolvedOutputPath,
                    duration_seconds = Math.Round(refactorStopwatch.Elapsed.TotalSeconds, 3),
                    success = false,
                    error = new
                    {
                        type = ex.GetType().FullName,
                        message = ex.Message,
                    }
                }
            );
        }
        catch
        {
            // ignore logging failures
        }

        throw;
    }
}

var prompt = await PromptLoader.LoadAsync(options.Settings.PromptPath, cancellationSource.Token);

var stopwatch = Stopwatch.StartNew();
var startedUtcGen = DateTimeOffset.UtcNow;
try
{
    var result = await generationService.GenerateCodeWithStatsAsync(prompt, generationOptions, cancellationSource.Token);
    stopwatch.Stop();

    await OutputWriter.WriteAsync(options.Settings.OutputPath, result.Text, cancellationSource.Token);

    RunStatsLogger.Append(
        runLogPath,
        new
        {
            timestamp_utc = startedUtcGen,
            mode = "generate",
            deployment = options.Settings.DeploymentName,
            endpoint = new Uri(options.Settings.Endpoint).Host,
            prompt_path = Path.GetFullPath(options.Settings.PromptPath),
            output_file = Path.GetFullPath(options.Settings.OutputPath),
            duration_seconds = Math.Round(stopwatch.Elapsed.TotalSeconds, 3),
            retry = new
            {
                attempts = result.RetryStats.Attempts,
                dropped_max_tokens = result.RetryStats.DroppedMaxTokens,
                dropped_temperature = result.RetryStats.DroppedTemperature,
            },
            output_chars = result.Text?.Length ?? 0,
            success = true,
        }
    );

    Console.WriteLine($"Generation completed in {stopwatch.Elapsed.TotalSeconds.ToString("F2", CultureInfo.InvariantCulture)} seconds.");
    Console.WriteLine($"Generated code written to: {options.Settings.OutputPath}");
    Console.WriteLine($"Run stats appended to: {runLogPath}");
}
catch (Exception ex)
{
    stopwatch.Stop();
    try
    {
        RunStatsLogger.Append(
            runLogPath,
            new
            {
                timestamp_utc = startedUtcGen,
                mode = "generate",
                deployment = options.Settings.DeploymentName,
                endpoint = new Uri(options.Settings.Endpoint).Host,
                prompt_path = Path.GetFullPath(options.Settings.PromptPath),
                output_file = Path.GetFullPath(options.Settings.OutputPath),
                duration_seconds = Math.Round(stopwatch.Elapsed.TotalSeconds, 3),
                success = false,
                error = new
                {
                    type = ex.GetType().FullName,
                    message = ex.Message,
                }
            }
        );
    }
    catch
    {
        // ignore logging failures
    }

    throw;
}

static string ResolvePath(string raw)
{
    if (string.IsNullOrWhiteSpace(raw))
    {
        return raw;
    }

    var candidate = raw.Trim().Trim('"');
    return Path.IsPathRooted(candidate)
        ? candidate
        : Path.GetFullPath(Path.Combine(Directory.GetCurrentDirectory(), candidate));
}

static string ResolveRefactorOutputPath(string configuredPath, string patchOutputDirectory)
{
    if (string.IsNullOrWhiteSpace(configuredPath))
    {
        Directory.CreateDirectory(patchOutputDirectory);
        return Path.GetFullPath(Path.Combine(patchOutputDirectory, "refactor.patch"));
    }

    var candidate = configuredPath.Trim().Trim('"');
    if (Path.IsPathRooted(candidate))
    {
        return Path.GetFullPath(candidate);
    }

    // Treat relative outputs as patch artifacts; store them under AzureOpenAICodeGen/runs/patches/
    Directory.CreateDirectory(patchOutputDirectory);

    while (candidate.StartsWith("./", StringComparison.Ordinal) || candidate.StartsWith(".\\", StringComparison.Ordinal))
    {
        candidate = candidate[2..];
    }

    return Path.GetFullPath(Path.Combine(patchOutputDirectory, candidate));
}

static string BuildGitApplyPatch(string displayPath, string targetPath, string originalText, string modelText)
{
    var sanitized = StripMarkdownFences(modelText);
    if (LooksLikeUnifiedDiff(sanitized))
    {
        return sanitized;
    }

    // If the model returned the full file, or returned headers + full file, extract the new file content.
    var newFileText = ExtractPhpFileContent(sanitized);
    if (string.IsNullOrWhiteSpace(newFileText))
    {
        throw new InvalidOperationException("Refactor output was empty after sanitization.");
    }

    if (originalText.Trim().StartsWith("<?php", StringComparison.Ordinal) && !newFileText.TrimStart().StartsWith("<?php", StringComparison.Ordinal))
    {
        throw new InvalidOperationException("Refactor output did not include full PHP file content (missing '<?php').");
    }

    return DiffWithGitNoIndex(displayPath, targetPath, newFileText);
}

static bool LooksLikeUnifiedDiff(string text)
{
    if (string.IsNullOrWhiteSpace(text))
    {
        return false;
    }

    var t = text.TrimStart();
    return t.StartsWith("diff --git ", StringComparison.Ordinal)
        || (t.Contains("\n--- ", StringComparison.Ordinal) && t.Contains("\n+++ ", StringComparison.Ordinal) && t.Contains("\n@@", StringComparison.Ordinal));
}

static string StripMarkdownFences(string text)
{
    if (string.IsNullOrWhiteSpace(text))
    {
        return string.Empty;
    }

    var trimmed = text.Trim();
    if (!trimmed.StartsWith("```", StringComparison.Ordinal))
    {
        return trimmed;
    }

    var lines = trimmed.Split(new[] { "\r\n", "\n" }, StringSplitOptions.None).ToList();
    if (lines.Count == 0)
    {
        return trimmed;
    }

    // Drop opening fence.
    lines.RemoveAt(0);
    // Drop closing fence if present.
    if (lines.Count > 0 && lines[^1].TrimStart().StartsWith("```", StringComparison.Ordinal))
    {
        lines.RemoveAt(lines.Count - 1);
    }

    return string.Join("\n", lines).Trim();
}

static string ExtractPhpFileContent(string text)
{
    if (string.IsNullOrWhiteSpace(text))
    {
        return string.Empty;
    }

    var trimmed = text.TrimStart();
    if (trimmed.StartsWith("<?php", StringComparison.Ordinal))
    {
        return trimmed;
    }

    // Common failure mode: model returns diff headers then the full file.
    var idx = trimmed.IndexOf("<?php", StringComparison.Ordinal);
    if (idx >= 0)
    {
        return trimmed[idx..];
    }

    return trimmed;
}

static string DiffWithGitNoIndex(string displayPath, string originalPath, string newFileText)
{
    var tempPath = Path.Combine(Path.GetTempPath(), $"refactor_{Guid.NewGuid():N}.php");
    try
    {
        File.WriteAllText(tempPath, newFileText, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));

        var psi = new ProcessStartInfo
        {
            FileName = "git",
            Arguments = $"diff --no-index -- \"{originalPath}\" \"{tempPath}\"",
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };

        using var proc = Process.Start(psi);
        if (proc is null)
        {
            throw new InvalidOperationException("Failed to start 'git' process.");
        }

        var stdout = proc.StandardOutput.ReadToEnd();
        var stderr = proc.StandardError.ReadToEnd();
        proc.WaitForExit();

        // Exit code 1 is expected when there are diffs.
        if (proc.ExitCode != 0 && proc.ExitCode != 1)
        {
            throw new InvalidOperationException($"git diff failed (exit {proc.ExitCode}): {stderr}");
        }

        var patch = RewriteGitNoIndexHeaders(stdout, displayPath, originalPath, tempPath);
        if (!LooksLikeUnifiedDiff(patch))
        {
            throw new InvalidOperationException("Generated patch did not look like a unified diff.");
        }

        return patch;
    }
    finally
    {
        try
        {
            if (File.Exists(tempPath))
            {
                File.Delete(tempPath);
            }
        }
        catch
        {
            // ignore temp cleanup failures
        }
    }
}

static string RewriteGitNoIndexHeaders(string patch, string displayPath, string originalPath, string tempPath)
{
    if (string.IsNullOrWhiteSpace(patch))
    {
        return string.Empty;
    }

    var lines = patch.Replace("\r\n", "\n").Split('\n');
    var rewritten = new List<string>(lines.Length);

    foreach (var line in lines)
    {
        if (line.StartsWith("diff --git ", StringComparison.Ordinal))
        {
            rewritten.Add($"diff --git a/{displayPath} b/{displayPath}");
            continue;
        }

        if (line.StartsWith("--- ", StringComparison.Ordinal))
        {
            rewritten.Add($"--- a/{displayPath}");
            continue;
        }

        if (line.StartsWith("+++ ", StringComparison.Ordinal))
        {
            rewritten.Add($"+++ b/{displayPath}");
            continue;
        }

        rewritten.Add(line);
    }

    return string.Join("\n", rewritten).TrimEnd() + "\n";
}

static string ReadAllTextBestEffort(string path)
{
    try
    {
        return File.ReadAllText(path, Encoding.UTF8);
    }
    catch
    {
        return File.ReadAllText(path, Encoding.Latin1);
    }
}

static string NormalizeForDiff(string absolutePath)
{
    var normalized = absolutePath.Replace('\\', '/');

    // Prefer repo-relative paths in diffs when possible.
    var marker = "/SuiteCRM/";
    var idx = normalized.IndexOf(marker, StringComparison.OrdinalIgnoreCase);
    if (idx >= 0)
    {
        return normalized[(idx + marker.Length)..];
    }

    return Path.GetFileName(normalized);
}

static void LoadDotEnv()
{
    try
    {
        Env.TraversePath().Load();
    }
    catch (FileNotFoundException)
    {
        // .env is optional
    }

    // Prefer the repo-local deployment name from .env over any stale machine/user environment variable.
    var deploymentFromDotEnv = TryReadDotEnvValue("AZURE_OPENAI_DEPLOYMENT");
    if (!string.IsNullOrWhiteSpace(deploymentFromDotEnv))
    {
        Environment.SetEnvironmentVariable("AZURE_OPENAI_DEPLOYMENT", deploymentFromDotEnv);
    }

    var key = Environment.GetEnvironmentVariable("AZURE_OPENAI_KEY");
    if (string.IsNullOrWhiteSpace(key))
    {
        var apiKey = Environment.GetEnvironmentVariable("AZURE_OPENAI_API_KEY");
        if (!string.IsNullOrWhiteSpace(apiKey))
        {
            Environment.SetEnvironmentVariable("AZURE_OPENAI_KEY", apiKey);
        }
    }
}

static string? TryReadDotEnvValue(string key)
{
    try
    {
        var directory = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (directory is not null)
        {
            var envPath = Path.Combine(directory.FullName, ".env");
            if (File.Exists(envPath))
            {
                foreach (var rawLine in File.ReadLines(envPath))
                {
                    var line = rawLine.Trim();
                    if (string.IsNullOrWhiteSpace(line) || line.StartsWith('#') || !line.Contains('='))
                    {
                        continue;
                    }

                    var parts = line.Split('=', 2);
                    var k = parts[0].Trim();
                    if (!string.Equals(k, key, StringComparison.Ordinal))
                    {
                        continue;
                    }

                    var value = parts[1].Trim().Trim('"').Trim('\'');
                    return string.IsNullOrWhiteSpace(value) ? null : value;
                }

                return null;
            }

            directory = directory.Parent;
        }
    }
    catch
    {
        // best-effort only
    }

    return null;
}

static IConfiguration BuildConfiguration(string[] args)
{
    var environment = Environment.GetEnvironmentVariable("DOTNET_ENVIRONMENT") ?? "Production";

    return new ConfigurationBuilder()
        .SetBasePath(Directory.GetCurrentDirectory())
        .AddJsonFile("appsettings.json", optional: true, reloadOnChange: false)
        .AddJsonFile($"appsettings.{environment}.json", optional: true, reloadOnChange: false)
        .AddEnvironmentVariables()
        .AddCommandLine(args)
        .Build();
}

static void ValidateSettings(AzureOpenAISettings settings)
{
    if (string.IsNullOrWhiteSpace(settings.Endpoint))
    {
        throw new InvalidOperationException("Azure OpenAI endpoint is required. Set AZURE_OPENAI_ENDPOINT or configure appsettings.");
    }

    if (string.IsNullOrWhiteSpace(settings.ApiKey))
    {
        throw new InvalidOperationException("Azure OpenAI API key is required. Set AZURE_OPENAI_KEY in the environment.");
    }

    if (string.IsNullOrWhiteSpace(settings.DeploymentName))
    {
        throw new InvalidOperationException("Azure OpenAI deployment name is required. Set AZURE_OPENAI_DEPLOYMENT.");
    }

    if (string.IsNullOrWhiteSpace(settings.PromptPath))
    {
        throw new InvalidOperationException("Prompt path is not configured.");
    }

    if (string.IsNullOrWhiteSpace(settings.OutputPath))
    {
        throw new InvalidOperationException("Output path is not configured.");
    }
}
