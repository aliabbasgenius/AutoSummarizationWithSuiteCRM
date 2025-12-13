namespace AzureOpenAICodeGen.Models;

internal sealed class GenerationOptions
{
    public string SystemPrompt { get; set; } = string.Empty;
    public double Temperature { get; set; } = 0.2;
    public int MaxOutputTokens { get; set; } = 1200;
}
