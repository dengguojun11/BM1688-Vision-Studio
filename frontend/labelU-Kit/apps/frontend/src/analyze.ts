import * as storage from '@/utils/storage';

// ==========================【埋点】=========================
const correctInfo = {
  token: '5ea1ddb69d6519b6',
  host: 'labelu.shlab.tech',
  api: 'https://analyze.shlab.tech/api/v1/log/create',
};

const userid = storage.get('userid');
const username = storage.get('username');

/** 仅官方在线演示环境启用；本地 / 私有化部署不加载埋点 */
function shouldEnableAnalyze(): boolean {
  if (import.meta.env.DEV) {
    return false;
  }
  if (!window.IS_ONLINE) {
    return false;
  }
  const host = location.hostname;
  if (host === 'localhost' || host === '127.0.0.1' || host === '::1') {
    return false;
  }
  if (import.meta.env.VITE_DISABLE_ANALYZE === '1') {
    return false;
  }
  return true;
}

function main() {
  if (!shouldEnableAnalyze()) {
    return;
  }

  if (!window.AnalyzeWiz) {
    console.warn('未引入埋点脚本');
    return;
  }

  if (userid) {
    window.AnalyzeWiz.setUser({
      id: `${username}-${userid}`,
      name: username,
    });
  } else {
    window.AnalyzeWiz.setUser({
      id: 'unlogin',
      name: '未登录',
    });
  }

  window.AnalyzeWiz.init({
    organize: 'labelu-online',
    delay: 0,
    token: correctInfo.token,
    api: correctInfo.api,
    actions: [
      {
        name: 'DOMContentLoaded',
        selector: 'window',
        handler: () => {
          return {
            type: 'page_view',
            resourceType: 'page',
            resourceId: location.pathname + location.search + location.hash,
            unaryResourceParams: null,
          };
        },
      },
      {
        name: 'click',
        selector: '[data-wiz="task-create"]',
        data: {
          type: 'button_click',
          resourceType: 'button',
          resourceId: 'task-create',
          unaryResourceParams: null,
        },
      },
      {
        name: 'click',
        selector: '[data-wiz="documentation"]',
        data: {
          type: 'button_click',
          resourceType: 'button',
          resourceId: 'documentation',
          unaryResourceParams: null,
        },
      },
      {
        name: 'click',
        selector: '[data-wiz="local-deploy-top-right"]',
        data: {
          type: 'button_click',
          resourceType: 'button',
          resourceId: 'local-deploy-top-right',
          unaryResourceParams: null,
        },
      },
      {
        name: 'click',
        selector: '[data-wiz="local-deploy-alert"]',
        data: {
          type: 'button_click',
          resourceType: 'button',
          resourceId: 'local-deploy-alert',
          unaryResourceParams: null,
        },
      },
    ],
  });
}

main();
