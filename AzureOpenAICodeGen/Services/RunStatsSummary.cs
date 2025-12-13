using System.Globalization;
using System.Text.Json;

namespace AzureOpenAICodeGen.Services;

internal static class RunStatsSummary
{
    internal sealed record Summary(
        int Total,
        int Success,
        int Failure,
        int Refactor,
        int Generate,
        double AvgDurationAll,
        double AvgDurationRefactor,
        double AvgDurationGenerate,
        double AvgAttempts,
        int DroppedMaxTokens,
        int DroppedTemperature);

    public static IEnumerable<JsonDocument> ReadJsonl(string path)
    {
        if (!File.Exists(path))
        {
            yield break;
        }

        foreach (var line in File.ReadLines(path))
        {
            if (string.IsNullOrWhiteSpace(line))
            {
                continue;
            }

            JsonDocument? doc = null;
            try
            {
                doc = JsonDocument.Parse(line);
            }
            catch
            {
                doc?.Dispose();
                continue;
            }

            yield return doc;
        }
    }

    public static Summary Compute(IReadOnlyList<JsonDocument> docs)
    {
        var total = docs.Count;
        var success = 0;
        var failure = 0;
        var refactor = 0;
        var generate = 0;

        var durationAll = new List<double>();
        var durationRefactor = new List<double>();
        var durationGenerate = new List<double>();

        var attempts = new List<double>();
        var droppedMaxTokens = 0;
        var droppedTemperature = 0;

        foreach (var root in docs.Select(doc => doc.RootElement))
        {
            ApplySuccess(root, ref success, ref failure);

            var mode = GetString(root, "mode");
            ApplyMode(mode, ref refactor, ref generate);

            ApplyDuration(root, mode, durationAll, durationRefactor, durationGenerate);
            ApplyRetry(root, attempts, ref droppedMaxTokens, ref droppedTemperature);
        }

        return new Summary(
            Total: total,
            Success: success,
            Failure: failure,
            Refactor: refactor,
            Generate: generate,
            AvgDurationAll: Avg(durationAll),
            AvgDurationRefactor: Avg(durationRefactor),
            AvgDurationGenerate: Avg(durationGenerate),
            AvgAttempts: Avg(attempts),
            DroppedMaxTokens: droppedMaxTokens,
            DroppedTemperature: droppedTemperature);
    }

    private static void ApplySuccess(JsonElement root, ref int success, ref int failure)
    {
        if (root.TryGetProperty("success", out var prop) && prop.ValueKind == JsonValueKind.True)
        {
            success++;
        }
        else if (root.TryGetProperty("success", out var prop2) && prop2.ValueKind == JsonValueKind.False)
        {
            failure++;
        }
    }

    private static void ApplyMode(string mode, ref int refactor, ref int generate)
    {
        if (mode.Equals("refactor", StringComparison.OrdinalIgnoreCase))
        {
            refactor++;
        }
        else if (mode.Equals("generate", StringComparison.OrdinalIgnoreCase))
        {
            generate++;
        }
    }

    private static void ApplyDuration(
        JsonElement root,
        string mode,
        List<double> durationAll,
        List<double> durationRefactor,
        List<double> durationGenerate)
    {
        if (!TryGetDouble(root, "duration_seconds", out var d))
        {
            return;
        }

        durationAll.Add(d);

        if (mode.Equals("refactor", StringComparison.OrdinalIgnoreCase))
        {
            durationRefactor.Add(d);
        }
        else if (mode.Equals("generate", StringComparison.OrdinalIgnoreCase))
        {
            durationGenerate.Add(d);
        }
    }

    private static void ApplyRetry(JsonElement root, List<double> attempts, ref int droppedMaxTokens, ref int droppedTemperature)
    {
        if (!root.TryGetProperty("retry", out var retryProp) || retryProp.ValueKind != JsonValueKind.Object)
        {
            return;
        }

        if (TryGetDouble(retryProp, "attempts", out var a))
        {
            attempts.Add(a);
        }

        if (retryProp.TryGetProperty("dropped_max_tokens", out var dm) && dm.ValueKind == JsonValueKind.True)
        {
            droppedMaxTokens++;
        }

        if (retryProp.TryGetProperty("dropped_temperature", out var dt) && dt.ValueKind == JsonValueKind.True)
        {
            droppedTemperature++;
        }
    }

    private static string GetString(JsonElement element, string property)
    {
        return element.TryGetProperty(property, out var prop) && prop.ValueKind == JsonValueKind.String
            ? prop.GetString() ?? string.Empty
            : string.Empty;
    }

    public static void Print(string path, IReadOnlyList<JsonDocument> docs)
    {
        var summary = Compute(docs);

        Console.WriteLine($"Stats file: {path}");
        Console.WriteLine($"Total: {summary.Total} | Success: {summary.Success} | Failure: {summary.Failure}");
        Console.WriteLine($"Modes: refactor={summary.Refactor} generate={summary.Generate}");
        Console.WriteLine($"Avg duration (s): all={summary.AvgDurationAll:F2} refactor={summary.AvgDurationRefactor:F2} generate={summary.AvgDurationGenerate:F2}");
        Console.WriteLine($"Avg attempts: {summary.AvgAttempts:F2} | Dropped max_tokens: {summary.DroppedMaxTokens} | Dropped temperature: {summary.DroppedTemperature}");

        Console.WriteLine();
        Console.WriteLine("Last 10 runs:");

        foreach (var doc in docs.TakeLast(10))
        {
            using var _ = doc;
            var root = doc.RootElement;
            var ts = root.TryGetProperty("timestamp_utc", out var tsProp) ? tsProp.ToString() : "";
            var mode = root.TryGetProperty("mode", out var modeProp) ? modeProp.ToString() : "";
            var ok = root.TryGetProperty("success", out var okProp) ? okProp.ToString() : "";
            var dur = root.TryGetProperty("duration_seconds", out var durProp) ? durProp.ToString() : "";

            var attemptsText = "";
            if (root.TryGetProperty("retry", out var retryProp) && retryProp.ValueKind == JsonValueKind.Object)
            {
                attemptsText = retryProp.TryGetProperty("attempts", out var a) ? a.ToString() : "";
            }

            Console.WriteLine($"- {ts} | {mode} | success={ok} | duration={dur}s | attempts={attemptsText}");
        }
    }

    private static bool TryGetDouble(JsonElement element, string property, out double value)
    {
        value = 0;
        if (!element.TryGetProperty(property, out var prop))
        {
            return false;
        }

        if (prop.ValueKind == JsonValueKind.Number && prop.TryGetDouble(out value))
        {
            return true;
        }

        if (prop.ValueKind == JsonValueKind.String)
        {
            var s = prop.GetString();
            return double.TryParse(s, NumberStyles.Any, CultureInfo.InvariantCulture, out value);
        }

        return false;
    }

    private static double Avg(List<double> values)
        => values.Count == 0 ? 0 : values.Average();
}
