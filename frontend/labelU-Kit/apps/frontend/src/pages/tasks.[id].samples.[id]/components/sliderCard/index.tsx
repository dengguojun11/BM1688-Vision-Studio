import { VideoCard, AudioCard } from '@labelu/components-react';
import { useTranslation } from '@labelu/i18n';
import { memo, useCallback, useMemo } from 'react';

import type { SampleResponse } from '@/api/types';
import { MediaType } from '@/api/types';
import { ReactComponent as CheckSvgIcon } from '@/assets/svg/check.svg';
import type { TaskSampleUser } from '@/hooks/useSampleWs';
import useMe from '@/hooks/useMe';
import { UserAvatar } from '@/components/UserAvatar';

import {
  CheckBg,
  Triangle,
  ContentWrapper,
  IdWrapper,
  SkipWrapper,
  ImageWrapper,
  AnnotatingUser,
  ThumbnailPlaceholder,
} from './style';

type TranslationHook = () => {
  t: (key: string, options?: Record<string, unknown>) => string;
};

const useTranslationCompat = useTranslation as unknown as TranslationHook;

function CheckIcon() {
  return (
    <CheckBg>
      <Triangle />
      <CheckSvgIcon />
    </CheckBg>
  );
}

interface SliderCardProps {
  cardInfo: SampleResponse;
  type?: MediaType;
  index?: number;
  onClick: (sample: SampleResponse) => void;
  editingUser?: TaskSampleUser;
  isActive: boolean;
}

const SliderCard = ({ type, cardInfo, editingUser, index, onClick, isActive }: SliderCardProps) => {
  const { inner_id, state, file } = cardInfo;
  const filename = file.filename;
  const url = useMemo(() => {
    return [MediaType.VIDEO, MediaType.AUDIO].includes(type as MediaType)
      ? file.url.replace('attachment', 'partial')
      : file.url;
  }, [type, file.url]);
  const { t } = useTranslationCompat();
  const me = useMe();
  const isMeTheCurrentEditingUser = editingUser?.user_id === me?.data?.id;

  const userBlock = useMemo(() => {
    if (!isMeTheCurrentEditingUser && editingUser) {
      return (
        <AnnotatingUser>
          <UserAvatar user={editingUser} /> <span className="annotating-text">{t('isAnnotating')}</span>
        </AnnotatingUser>
      );
    }

    return null;
  }, [editingUser, isMeTheCurrentEditingUser, t]);

  const handleOnClick = useCallback(() => {
    if (isActive) {
      return;
    }

    onClick(cardInfo);
  }, [cardInfo, isActive, onClick]);

  if (type === MediaType.AUDIO) {
    return (
      <ImageWrapper items="stretch">
        <AudioCard
          src={url!}
          active={isActive}
          onClick={handleOnClick}
          title={filename.substring(9)}
          no={index! + 1}
          showNo
          completed={state === 'DONE'}
          skipped={state === 'SKIPPED'}
        />
      </ImageWrapper>
    );
  }

  if (type === MediaType.VIDEO) {
    return (
      <ImageWrapper>
        {userBlock}

        <VideoCard
          src={url!}
          title={inner_id}
          active={isActive}
          onClick={handleOnClick}
          showPlayIcon
          showDuration
          completed={state === 'DONE'}
          skipped={state === 'SKIPPED'}
        />
      </ImageWrapper>
    );
  }

  const thumbnail = file.thumbnail_url;

  return (
    <ImageWrapper items="center" flex="column" justify="center">
      {userBlock}
      <ContentWrapper
        flex="column"
        items="center"
        justify="center"
        className={isActive ? 'is-active' : undefined}
        onClick={handleOnClick}
      >
        {type === MediaType.IMAGE &&
          (thumbnail ? <img src={thumbnail} alt="" loading="lazy" decoding="async" /> : <ThumbnailPlaceholder />)}
        {state === 'DONE' && <CheckIcon />}
        {state === 'SKIPPED' && <SkipWrapper>{t('skipped')}</SkipWrapper>}
        {state === 'INCOMPLETE' && <SkipWrapper incomplete>不完整</SkipWrapper>}
        {state === 'ABNORMAL' && <SkipWrapper abnormal>{t('abnormal')}</SkipWrapper>}
      </ContentWrapper>
      <IdWrapper>{inner_id}</IdWrapper>
    </ImageWrapper>
  );
};

export default memo(SliderCard);
