import { SearchOutlined } from '@ant-design/icons';
import { Input } from 'antd';
import type { CSSProperties } from 'react';

export interface ServerSearchInputProps {
  value: string;
  onChange: (value: string) => void;
  onSearch: (value: string) => void;
  placeholder?: string;
  ariaLabel?: string;
  loading?: boolean;
  style?: CSSProperties;
  className?: string;
  allowClear?: boolean;
}

/**
 * Shared server-backed search control.
 *
 * The input deliberately does not debounce or fetch by itself: callers decide
 * when a query becomes authoritative, so URL-driven pages and modal lists can
 * share the same interaction without creating duplicate requests.
 */
export default function ServerSearchInput({
  value,
  onChange,
  onSearch,
  placeholder = '搜索',
  ariaLabel = '搜索',
  loading = false,
  style,
  className,
  allowClear = true,
}: ServerSearchInputProps) {
  return (
    <Input.Search
      value={value}
      allowClear={allowClear}
      maxLength={128}
      placeholder={placeholder}
      aria-label={ariaLabel}
      loading={loading}
      className={className}
      enterButton={(
        <span aria-label={ariaLabel} title={ariaLabel}>
          <SearchOutlined aria-hidden="true" />
        </span>
      )}
      style={style}
      onChange={(event) => onChange(event.target.value)}
      onSearch={(next) => onSearch(next.trim())}
    />
  );
}
