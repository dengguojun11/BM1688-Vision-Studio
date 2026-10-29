import { redirect } from 'react-router';

import { goRegister } from '@/utils/sso';
import * as storage from '@/utils/storage';

export async function registerLoader() {
  if (storage.get('token')) {
    return redirect('/workspace');
  }

  // 线上环境继续交给统一身份系统注册；本地环境使用平台自带注册页。
  if (window.IS_ONLINE) {
    goRegister();
    return Promise.resolve();
  }

  return null;
}
