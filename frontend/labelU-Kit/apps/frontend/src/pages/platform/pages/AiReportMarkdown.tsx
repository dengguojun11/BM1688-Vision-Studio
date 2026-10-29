import type { CSSProperties, ReactNode } from 'react';
import { Fragment } from 'react';
import styled from 'styled-components';

const ReportMarkdownRoot = styled.article`
  color: #334155;
  font-size: 14px;
  line-height: 1.85;
  overflow-wrap: break-word;

  h1,
  h2,
  h3,
  h4 {
    color: #0f172a;
    font-weight: 650;
    line-height: 1.45;
    scroll-margin-top: 16px;
  }

  h1 {
    margin: 0 0 20px;
    padding-bottom: 14px;
    border-bottom: 1px solid #e2e8f0;
    font-size: 22px;
  }

  h2 {
    margin: 26px 0 14px;
    padding: 9px 12px;
    border-left: 3px solid #1677ff;
    border-radius: 0 6px 6px 0;
    background: #f1f6ff;
    font-size: 17px;
  }

  h3 {
    margin: 22px 0 10px;
    font-size: 15px;
  }

  h4 {
    margin: 18px 0 8px;
    font-size: 14px;
  }

  p {
    margin: 0 0 12px;
  }

  strong {
    color: #172033;
    font-weight: 650;
  }

  ul,
  ol {
    margin: 8px 0 16px;
    padding-left: 26px;
  }

  li {
    margin: 5px 0;
    padding-left: 3px;
  }

  li::marker {
    color: #64748b;
  }

  li.report-nested-item {
    margin-left: 20px;
  }

  code {
    padding: 2px 5px;
    border: 1px solid #e2e8f0;
    border-radius: 4px;
    background: #f1f5f9;
    color: #b4235a;
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
    font-size: 0.9em;
    overflow-wrap: anywhere;
  }

  pre {
    margin: 12px 0 18px;
    padding: 16px 18px;
    border-radius: 8px;
    background: #0f172a;
    color: #e2e8f0;
    font-size: 12px;
    line-height: 1.7;
    overflow: auto;
  }

  pre code {
    padding: 0;
    border: 0;
    background: transparent;
    color: inherit;
    font-size: inherit;
    overflow-wrap: normal;
  }

  hr {
    margin: 24px 0;
    border: 0;
    border-top: 1px solid #e2e8f0;
  }

  blockquote {
    margin: 12px 0;
    padding: 8px 14px;
    border-left: 3px solid #94a3b8;
    background: #f8fafc;
    color: #475569;
  }

  .report-table-scroll {
    width: 100%;
    margin: 12px 0 18px;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    overflow-x: auto;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    line-height: 1.65;
  }

  th,
  td {
    min-width: 88px;
    padding: 9px 12px;
    border-right: 1px solid #e2e8f0;
    border-bottom: 1px solid #e2e8f0;
    text-align: left;
    vertical-align: top;
  }

  th:last-child,
  td:last-child {
    border-right: 0;
  }

  tr:last-child td {
    border-bottom: 0;
  }

  th {
    background: #f1f5f9;
    color: #1e293b;
    font-weight: 650;
    white-space: nowrap;
  }

  tbody tr:nth-child(even) td {
    background: #fafcff;
  }

  @media (max-width: 720px) {
    font-size: 14px;
    line-height: 1.8;

    h1 {
      font-size: 19px;
    }

    h2 {
      margin-top: 22px;
      font-size: 16px;
    }

    th,
    td {
      padding: 8px 10px;
    }

    pre {
      padding: 13px 14px;
    }
  }
`;

function inlineMarkdown(text: string): ReactNode {
  const tokens = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return tokens.map((token, index) => {
    if (token.startsWith('**') && token.endsWith('**') && token.length > 4) {
      return <strong key={index}>{token.slice(2, -2)}</strong>;
    }
    if (token.startsWith('`') && token.endsWith('`') && token.length > 2) {
      return <code key={index}>{token.slice(1, -1)}</code>;
    }
    return <Fragment key={index}>{token}</Fragment>;
  });
}

function tableCells(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  return trimmed.split('|').map((cell) => cell.trim());
}

function isTableDivider(line: string): boolean {
  const cells = tableCells(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s/g, '')));
}

function tableAlignment(divider: string): CSSProperties['textAlign'] {
  const normalized = divider.replace(/\s/g, '');
  if (normalized.startsWith(':') && normalized.endsWith(':')) return 'center';
  if (normalized.endsWith(':')) return 'right';
  return 'left';
}

function isSpecialBlock(lines: string[], index: number): boolean {
  const line = lines[index] || '';
  return (
    !line.trim() ||
    /^\s*```/.test(line) ||
    /^\s{0,3}#{1,4}\s+/.test(line) ||
    /^\s*(?:[-*+]|\d+\.)\s+/.test(line) ||
    /^\s*>/.test(line) ||
    /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line) ||
    (line.includes('|') && isTableDivider(lines[index + 1] || ''))
  );
}

function renderHeading(line: string, key: string): ReactNode {
  const match = line.match(/^\s{0,3}(#{1,4})\s+(.+)$/);
  if (!match) return null;
  const level = match[1].length;
  const content = inlineMarkdown(match[2]);
  if (level === 1) return <h1 key={key}>{content}</h1>;
  if (level === 2) return <h2 key={key}>{content}</h2>;
  if (level === 3) return <h3 key={key}>{content}</h3>;
  return <h4 key={key}>{content}</h4>;
}

export function MarkdownBlocks({ source }: { source: string }) {
  const lines = source.replace(/\r\n?/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    if (/^\s*```/.test(line)) {
      const language = line.trim().slice(3).trim();
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !/^\s*```/.test(lines[index])) {
        code.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(
        <pre key={`code-${index}`} data-language={language || undefined}>
          <code>{code.join('\n')}</code>
        </pre>,
      );
      continue;
    }

    if (/^\s{0,3}#{1,4}\s+/.test(line)) {
      blocks.push(renderHeading(line, `heading-${index}`));
      index += 1;
      continue;
    }

    if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      blocks.push(<hr key={`divider-${index}`} />);
      index += 1;
      continue;
    }

    if (line.includes('|') && isTableDivider(lines[index + 1] || '')) {
      const headers = tableCells(line);
      const alignments = tableCells(lines[index + 1]).map(tableAlignment);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        rows.push(tableCells(lines[index]));
        index += 1;
      }
      blocks.push(
        <div className="report-table-scroll" key={`table-${index}`}>
          <table>
            <thead>
              <tr>
                {headers.map((header, cellIndex) => (
                  <th key={cellIndex} style={{ textAlign: alignments[cellIndex] || 'left' }}>
                    {inlineMarkdown(header)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {headers.map((_, cellIndex) => (
                    <td key={cellIndex} style={{ textAlign: alignments[cellIndex] || 'left' }}>
                      {inlineMarkdown(row[cellIndex] || '')}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    const listMatch = line.match(/^(\s*)([-*+]|\d+\.)\s+(.+)$/);
    if (listMatch) {
      const ordered = /\d+\./.test(listMatch[2]);
      const items: Array<{ content: string; nested: boolean }> = [];
      while (index < lines.length) {
        const itemMatch = lines[index].match(/^(\s*)([-*+]|\d+\.)\s+(.+)$/);
        if (!itemMatch || /\d+\./.test(itemMatch[2]) !== ordered) break;
        items.push({ content: itemMatch[3], nested: itemMatch[1].length >= 2 });
        index += 1;
      }
      const ListTag = ordered ? 'ol' : 'ul';
      blocks.push(
        <ListTag key={`list-${index}`}>
          {items.map((item, itemIndex) => (
            <li className={item.nested ? 'report-nested-item' : undefined} key={itemIndex}>
              {inlineMarkdown(item.content)}
            </li>
          ))}
        </ListTag>,
      );
      continue;
    }

    if (/^\s*>/.test(line)) {
      const quotes: string[] = [];
      while (index < lines.length && /^\s*>/.test(lines[index])) {
        quotes.push(lines[index].replace(/^\s*>\s?/, ''));
        index += 1;
      }
      blocks.push(<blockquote key={`quote-${index}`}>{inlineMarkdown(quotes.join(' '))}</blockquote>);
      continue;
    }

    const paragraph: string[] = [line.trim()];
    index += 1;
    while (index < lines.length && !isSpecialBlock(lines, index)) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(<p key={`paragraph-${index}`}>{inlineMarkdown(paragraph.join(' '))}</p>);
  }

  return <>{blocks}</>;
}

export default function AiReportMarkdown({ source }: { source: string }) {
  return (
    <ReportMarkdownRoot aria-label="AI 报告正文" data-testid="ai-report-markdown">
      <MarkdownBlocks source={source} />
    </ReportMarkdownRoot>
  );
}
