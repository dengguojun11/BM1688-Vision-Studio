import { useEffect, useMemo, useState } from 'react';
import { useParams, useRouteLoaderData } from 'react-router-dom';
import { Alert, Button, Checkbox, Modal, Pagination } from 'antd';
import { FlexLayout } from '@labelu/components-react';
import { CopyOutlined, ReloadOutlined, TeamOutlined } from '@ant-design/icons';
import styled from 'styled-components';
import type { CheckboxChangeEvent } from 'antd/es/checkbox';
import { useTranslation } from '@labelu/i18n';

type TranslationHook = () => {
  t: (key: string, options?: Record<string, unknown>) => string;
};

const useTranslationCompat = useTranslation as unknown as TranslationHook;

import { useCollaborators } from '@/api/queries/task';
import { UserAvatar } from '@/components/UserAvatar';
import ServerSearchInput from '@/components/ServerSearchInput';
import { useUsers } from '@/api/queries/user';
import {
  useAddCollaborator,
  useBatchAddCollaborators,
  useBatchRemoveCollaborators,
  useRemoveCollaborator,
} from '@/api/mutations/task';
import type { TaskLoaderResult } from '@/loaders/task.loader';
import { message } from '@/StaticAnt';
import type { UserResponse } from '@/api/types';

const StyledFlexLayout = styled(FlexLayout)`
  > div:nth-child(odd) {
    background-color: var(--color-fill-quaternary);
  }
`;

const FlexLayoutItem = styled(FlexLayout.Item)`
  padding: 0.5rem;
`;

export default function CollaboratorPortal() {
  const [open, setOpen] = useState(false);
  const routeParams = useParams();
  const routerData = useRouteLoaderData('task') as TaskLoaderResult;
  const task = routerData.task;
  const taskId = routeParams.taskId;
  const [params, setParams] = useState({
    page: 0,
    size: 10,
    q: '',
  });
  const [searchDraft, setSearchDraft] = useState('');
  const [selectedUserCache, setSelectedUserCache] = useState<Record<number, UserResponse>>({});
  const { t } = useTranslationCompat();
  const inputTaskId = taskId ? parseInt(taskId) : 0;
  const collaborators = useCollaborators(inputTaskId, open);
  const users = useUsers(params);
  const addCollaborator = useAddCollaborator(inputTaskId);
  const removeCollaborator = useRemoveCollaborator(inputTaskId);
  const batchAddCollaborators = useBatchAddCollaborators(inputTaskId);
  const batchRemoveCollaborators = useBatchRemoveCollaborators(inputTaskId);
  const collaboratorIds = useMemo(() => {
    return collaborators?.data?.map((item) => item.id) || [];
  }, [collaborators]);
  useEffect(() => {
    const currentCollaborators = collaborators?.data || [];
    if (!currentCollaborators.length) return;
    setSelectedUserCache((previous) => {
      const next = { ...previous };
      currentCollaborators.forEach((user) => {
        if (user.id) next[Number(user.id)] = user;
      });
      return next;
    });
  }, [collaborators?.data]);
  useEffect(() => {
    const currentUsers = users.data?.data || [];
    if (!currentUsers.length) return;
    setSelectedUserCache((previous) => {
      const next = { ...previous };
      currentUsers.forEach((user) => {
        if (user.id && collaboratorIds.includes(user.id)) next[Number(user.id)] = user;
      });
      return next;
    });
  }, [collaboratorIds, users.data?.data]);
  const usersExceptCreator = useMemo(() => {
    return users.data?.data?.filter((item) => item.id !== task?.created_by?.id) ?? [];
  }, [task?.created_by?.id, users.data?.data]);
  const visibleUsers = useMemo(() => {
    const current = usersExceptCreator.filter((user) => user.id);
    const currentIds = new Set(current.map((user) => Number(user.id)));
    const selectedOutsidePage = collaboratorIds
      .map((id) => (id ? selectedUserCache[Number(id)] : undefined))
      .filter(
        (user): user is UserResponse =>
          Boolean(user && user.id) && user?.id !== task?.created_by?.id,
      )
      .filter((user) => !currentIds.has(Number(user.id)));
    return [...selectedOutsidePage, ...current];
  }, [collaboratorIds, selectedUserCache, task?.created_by?.id, usersExceptCreator]);
  const isAllChecked = useMemo(() => {
    return usersExceptCreator.every((item) => collaboratorIds.includes(item.id!));
  }, [collaboratorIds, usersExceptCreator]);
  const isSomeChecked = useMemo(() => {
    return usersExceptCreator.some((item) => collaboratorIds.includes(item.id!));
  }, [collaboratorIds, usersExceptCreator]);
  const handleOpen = () => {
    setOpen(true);
  };
  const applySearch = (nextSearch: string) => {
    const normalized = nextSearch.trim();
    setSearchDraft(nextSearch);
    setParams((previous) => ({ ...previous, page: 0, q: normalized }));
  };
  const handleCollaboratorChange = async (e: CheckboxChangeEvent) => {
    if (e.target.checked) {
      await addCollaborator.mutateAsync(parseInt(e.target.value));
    } else {
      await removeCollaborator.mutateAsync(parseInt(e.target.value));
    }

    collaborators.refetch();
  };
  const handleCheckAllChange = async (e: CheckboxChangeEvent) => {
    if (e.target.checked) {
      const ids = usersExceptCreator
        ?.map((item) => item.id!)
        .filter((id) => !collaboratorIds.includes(id) && task?.created_by?.id !== id);

      if (ids) {
        await batchAddCollaborators.mutateAsync(ids);
      }
    } else {
      const ids = usersExceptCreator
        ?.map((item) => item.id!)
        .filter((id) => collaboratorIds.includes(id) && task?.created_by?.id !== id);

      if (ids) {
        await batchRemoveCollaborators.mutateAsync(ids);
      }
    }

    collaborators.refetch();
  };

  return (
    <>
      <Button type="text" icon={<TeamOutlined />} onClick={handleOpen}>
        {t('collaborators')}
      </Button>
      <Modal title={t('collaborators')} open={open} onCancel={() => setOpen(false)} footer={null}>
        <FlexLayout flex="column" gap="1rem" items="stretch">
          <Alert
            type="info"
            message={
              <div>
                {t('shareTaskLink')}
                <Button
                  type="link"
                  size="small"
                  icon={<CopyOutlined />}
                  onClick={() => {
                    navigator.clipboard.writeText(window.location.href);
                    message.success(t('copied'));
                  }}
                />
                {t('inviteRegister')}
                {t('invitationTips')}
                {t('afterInvitation')}
              </div>
            }
          />
          <FlexLayout flex="row" justify="space-between">
            <FlexLayout.Item flex="row" gap=".5rem" items="center">
              {t('users')}
              <Button
                type="text"
                size="small"
                icon={<ReloadOutlined />}
                onClick={() => users.refetch()}
                loading={users.isFetching}
              />
            </FlexLayout.Item>
            <FlexLayout.Item>
              <Checkbox
                onChange={handleCheckAllChange}
                checked={isAllChecked}
                indeterminate={!isAllChecked && isSomeChecked}
              >
                {t('selectAll')}
              </Checkbox>
            </FlexLayout.Item>
          </FlexLayout>
          <ServerSearchInput
            value={searchDraft}
            onChange={(nextValue) => {
              setSearchDraft(nextValue);
              if (!nextValue.trim()) applySearch('');
            }}
            onSearch={applySearch}
            placeholder="按用户 ID、账号或账号类型搜索"
            ariaLabel="搜索协作者"
            loading={users.isFetching}
          />
          <StyledFlexLayout flex="column">
            <FlexLayoutItem flex="row" items="center" justify="space-between">
              <UserAvatar
                shortName={false}
                showTooltip={false}
                user={task?.created_by}
                style={{ backgroundColor: 'var(--color-primary)', color: '#fff' }}
              />
              {t('creator')}
            </FlexLayoutItem>
            {visibleUsers.map((item) => (
              <FlexLayoutItem flex="row" key={item.id} items="center" justify="space-between">
                <UserAvatar
                  shortName={false}
                  showTooltip={false}
                  user={item}
                  style={{ backgroundColor: 'var(--color-primary)', color: '#fff' }}
                />
                <Checkbox
                  checked={collaboratorIds.includes(item.id)}
                  value={item.id}
                  onChange={handleCollaboratorChange}
                >
                  {collaboratorIds.includes(item.id) ? t('joined') : t('join')}
                </Checkbox>
              </FlexLayoutItem>
            ))}
            {!users.isFetching && !visibleUsers.length && (
              <FlexLayoutItem flex="row" justify="center">
                {params.q ? '没有匹配的用户' : '暂无可添加的用户'}
              </FlexLayoutItem>
            )}
          </StyledFlexLayout>
          <FlexLayout.Item flex="row" justify="center">
            <Pagination
              size="small"
              current={params.page + 1}
              pageSize={params.size}
              showSizeChanger
              pageSizeOptions={['10', '20', '50']}
              total={users.data?.meta_data?.total}
              onChange={(page, pageSize) => {
                setParams((previous) => ({
                  ...previous,
                  page: pageSize === previous.size ? page - 1 : 0,
                  size: pageSize || previous.size,
                }));
              }}
            />
          </FlexLayout.Item>
        </FlexLayout>
      </Modal>
    </>
  );
}
