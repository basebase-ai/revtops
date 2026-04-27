export function formatModelNameForUi(modelName: string): string {
  const normalized: string = modelName.trim();
  const lower: string = normalized.toLowerCase();

  // Ensure GPT-5.5 variants are always shown with the explicit 5.5 marker.
  if (lower === 'gpt-5.5' || lower === 'gpt5.5') return 'GPT-5.5';
  if (lower === 'gpt-5.5-mini' || lower === 'gpt5.5-mini') return 'GPT-5.5 mini';
  if (lower === 'gpt-5.5-nano' || lower === 'gpt5.5-nano') return 'GPT-5.5 nano';

  if (lower === 'gpt-5') return 'GPT-5';
  if (lower.startsWith('gpt-')) return `GPT-${normalized.slice(4)}`;
  if (lower.startsWith('gpt')) return `GPT-${normalized.slice(3)}`;

  return normalized;
}
