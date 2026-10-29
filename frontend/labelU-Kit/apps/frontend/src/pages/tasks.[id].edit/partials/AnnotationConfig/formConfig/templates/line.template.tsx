import { ArrowDownOutlined, ArrowUpOutlined } from '@ant-design/icons';
import type { RuleRender } from 'antd/es/form';
import { i18n } from '@labelu/i18n';

import type { FancyItemIdentifier } from '@/components/FancyInput/types';
import FancyInput from '@/components/FancyInput';

const translate = (key: string): string => String(i18n.t(key));

export default [
  {
    field: 'tool',
    key: 'tool',
    type: 'string',
    hidden: true,
    initialValue: 'lineTool',
  },
  {
    key: 'config',
    field: 'config',
    type: 'group',
    children: [
      {
        field: 'lineType',
        key: 'lineType',
        type: 'enum',
        label: translate('lineType'),
        initialValue: 0,
        antProps: {
          options: [
            { label: translate('straightLine'), value: 0 },
            { label: translate('spline'), value: 1 },
          ],
        },
      },
      {
        field: 'arrowType',
        key: 'arrowType',
        type: 'enum',
        label: translate('arrowType'),
        initialValue: 'none',
        antProps: {
          options: [
            { label: translate('single'), value: 'single' },
            { label: translate('double'), value: 'double' },
            { label: translate('none'), value: 'none' },
          ],
        },
        renderFormItem({ antProps, ...props }, form, fullField) {
          const lineType = form.getFieldValue([...(fullField as any[]).slice(0, -1), 'lineType']);

          if (lineType === 1) {
            return null;
          }

          return <FancyInput {...props} {...antProps} />;
        },
      },
      {
        type: 'group',
        key: 'pointNum',
        layout: 'horizontal',
        label: translate('pointNumber'),
        children: [
          {
            field: 'lowerLimitPointNum',
            key: 'lowerLimitPointNum',
            type: 'number',
            label: undefined,
            initialValue: 2,
            antProps: {
              addonAfter: <ArrowDownOutlined />,
              min: 0,
              placeholder: translate('minimumPointNumber'),
            },
            rules: [
              {
                required: true,
                message: translate('minimumPointNumberRequired'),
              },
              (({ getFieldValue }) => ({
                // @ts-ignore
                validator({ fullField }, value: number) {
                  const maxNum = getFieldValue(
                    fullField.replace('lowerLimitPointNum', 'upperLimitPointNum').split('.'),
                  );
                  if (value > maxNum) {
                    return Promise.reject(new Error(translate('minimumPointCannotExceedMaximum')));
                  }

                  return Promise.resolve();
                },
              })) as RuleRender,
            ],
          },
          {
            field: 'upperLimitPointNum',
            key: 'upperLimitPointNum',
            type: 'number',
            label: undefined,
            antProps: {
              addonAfter: <ArrowUpOutlined />,
              min: 0,
              placeholder: translate('maximumPointNumber'),
            },
            initialValue: 100,
            rules: [
              {
                required: true,
                message: translate('maximumPointNumberRequired'),
              },
              ({ getFieldValue }) => ({
                // @ts-ignore
                validator({ fullField }, value: number) {
                  const minNum = getFieldValue(
                    fullField.replace('upperLimitPointNum', 'lowerLimitPointNum').split('.'),
                  );
                  if (value < minNum) {
                    return Promise.reject(new Error(translate('maximumPointCannotSmallThanMinimumPoint')));
                  }

                  return Promise.resolve();
                },
              }),
            ],
          },
        ],
      },
      {
        field: 'edgeAdsorption',
        key: 'edgeAdsorption',
        type: 'boolean',
        label: translate('edgeAdsorption'),
        initialValue: false,
        renderFormItem({ antProps, ...props }, form, fullField) {
          const lineType = form.getFieldValue([...(fullField as any[]).slice(0, -1), 'lineType']);

          if (lineType === 1) {
            return null;
          }

          return <FancyInput {...props} {...antProps} />;
        },
      },
      {
        field: 'attributes',
        key: 'attributes',
        type: 'list-attribute',
        label: translate('labelConfig'),
        initialValue: [
          {
            color: '#ff6600',
            key: translate('label1'),
            value: 'label-1',
          },
        ],
      },
    ],
  },
] as FancyItemIdentifier[];
