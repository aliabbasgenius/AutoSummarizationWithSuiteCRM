namespace AzureOpenAICodeGen.Services;

internal static class OutputWriter
{
    public static async Task WriteAsync(string relativePath, string content, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(relativePath))
        {
            throw new ArgumentException("Output path must be provided.", nameof(relativePath));
        }

        var absolutePath = Path.GetFullPath(relativePath);
        Directory.CreateDirectory(Path.GetDirectoryName(absolutePath) ?? Directory.GetCurrentDirectory());

        await File.WriteAllTextAsync(absolutePath, content, cancellationToken);
    }
}
