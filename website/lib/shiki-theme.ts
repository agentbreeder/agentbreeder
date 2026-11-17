// A Shiki theme whose colours are CSS variables (design v2 §8.3): code blocks follow the page
// theme and the --syntax-* tokens in website/brand.css, whose contrast on --code-background is
// tested (tests/test_brand_tokens.py). No stock github-* theme. Used for both of fumadocs'
// light and dark slots, so `--shiki-light` and `--shiki-dark` resolve to the same variables.
const fg = 'var(--foreground)';
const key = 'var(--syntax-key)';
const str = 'var(--syntax-string)';
const num = 'var(--syntax-number)';
const comment = 'var(--syntax-comment)';

export const brandShikiTheme = {
  name: 'agentbreeder-tokens',
  type: 'light' as const,
  colors: {
    'editor.foreground': fg,
    'editor.background': 'var(--code-background)',
  },
  tokenColors: [
    { settings: { foreground: fg } },
    { scope: ['comment', 'punctuation.definition.comment'], settings: { foreground: comment } },
    {
      scope: ['string', 'string.quoted', 'string.unquoted', 'string.template', 'markup.inline.raw'],
      settings: { foreground: str },
    },
    {
      scope: ['constant.numeric', 'constant.language', 'constant.other', 'keyword.other.unit'],
      settings: { foreground: num },
    },
    {
      scope: [
        'entity.name.tag',
        'keyword',
        'storage',
        'support.type.property-name',
        'support.function',
        'entity.name.function',
        'entity.other.attribute-name',
        'variable.other.property',
        'meta.object-literal.key',
      ],
      settings: { foreground: key },
    },
    { scope: ['punctuation', 'keyword.operator', 'variable', 'meta.embedded'], settings: { foreground: fg } },
  ],
};
