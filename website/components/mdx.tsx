import defaultMdxComponents from 'fumadocs-ui/mdx';
import { CodeBlock, Pre } from 'fumadocs-ui/components/codeblock';
import { ScrollTable as Table } from './scroll-table';
import { isValidElement, type ComponentProps, type ReactNode } from 'react';

// The plain text of rendered MDX children (Shiki emits nested spans).
function textOf(node: ReactNode): string {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join('');
  if (isValidElement<{ children?: ReactNode }>(node)) return textOf(node.props.children);
  return '';
}

// fumadocs makes every code block's scroll viewport a focusable role="region" with no name, so a
// page with several blocks has several unnamed, identical landmarks (axe: landmark-unique). Name
// each one after its title or its first line, which also tells a screen-reader user what it is.
function CodeBlockPre(props: ComponentProps<'pre'> & { title?: string }) {
  const first = textOf(props.children).trim().split('\n')[0]?.slice(0, 60) ?? '';
  const label = props.title ? `Code: ${props.title}` : first ? `Code: ${first}` : 'Code';
  return (
    <CodeBlock {...props} viewportProps={{ 'aria-label': label }}>
      <Pre>{props.children}</Pre>
    </CodeBlock>
  );
}

// An empty header cell (a table's corner) is not a header: render it as a data cell so it names
// nothing (axe: empty-table-header). The docs' table content is unchanged.
function Th(props: ComponentProps<'th'>) {
  const { children } = props;
  const empty = children == null || (typeof children === 'string' && !children.trim());
  if (empty) return <td {...(props as ComponentProps<'td'>)} />;
  return <th {...props} />;
}

export const mdxComponents = { ...defaultMdxComponents, pre: CodeBlockPre, table: Table, th: Th };
