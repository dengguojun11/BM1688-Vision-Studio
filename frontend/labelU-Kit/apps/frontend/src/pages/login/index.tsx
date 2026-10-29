import type { FormProps } from 'antd';
import { Form } from 'antd';
import { EyeInvisibleOutlined, EyeOutlined } from '@ant-design/icons';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { i18n } from '@labelu/i18n';
import { useEffect, useRef, useState } from 'react';

import { login } from '@/api/services/user';
import { ReactComponent as EmailIcon } from '@/assets/svg/email.svg';
import { ReactComponent as PasswordIcon } from '@/assets/svg/password.svg';
import { message } from '@/StaticAnt';
import useMe from '@/hooks/useMe';
import { PRODUCT_TITLE, PRODUCT_TITLE_ZH } from '@/utils/product';
import { normalizeRedirect } from './loginRedirect';

import { OrbitField } from './OrbitField';
import {
  EvoBody,
  EvoBrandMark,
  EvoBrandSubTitle,
  EvoBrandTitle,
  EvoBrandWord,
  EvoCard,
  EvoCardSubTitle,
  EvoCardTitle,
  EvoCommandMark,
  EvoCenterSpacer,
  EvoControl,
  EvoDivider,
  EvoEyebrow,
  EvoField,
  EvoForget,
  EvoFormWrapper,
  EvoFrame,
  EvoHero,
  EvoHeroText,
  EvoHeroTitle,
  EvoInlineCommand,
  EvoKeywords,
  EvoLabel,
  EvoLoginButton,
  EvoLoginGlobalStyle,
  EvoLoginShell,
  EvoLogoArea,
  EvoNav,
  EvoRegisterText,
  EvoRegisterButton,
  EvoPasswordToggle,
  EvoTopBar,
  EvoTopEmpty,
} from './style';

interface FormValues {
  username: string;
  password: string;
}

const commandSnippets = ['evo train --self-learning', 'adapt --online --safe', 'sync intelligence --continuous'];
const commandMarks = ['_', '\\', '|', '/'];
const defaultFormValues: FormValues = {
  username: '',
  password: '',
};

const LoginPage = () => {
  const [commandText, setCommandText] = useState('');
  const [commandMark, setCommandMark] = useState(commandMarks[0]);
  const commandIndexRef = useRef(0);
  const commandCharIndexRef = useRef(0);
  const commandDeletingRef = useRef(false);
  const [showPassword, setShowPassword] = useState(false);
  const [form] = Form.useForm<FormValues>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const me = useMe();
  const loginText = String(i18n.t('login'));
  const emailText = String(i18n.t('email'));
  const passwordText = String(i18n.t('password'));
  const emailPleaseText = String(i18n.t('emailPlease'));
  const emailFormatPleaseText = String(i18n.t('emailFormatPlease'));
  const passwordPleaseText = String(i18n.t('passwordPlease'));
  const passwordCannotContainsWhitespaceText = String(i18n.t('passwordCannotContainsWhitespace'));
  const passwordLengthErrorText = String(i18n.t('passwordLengthError'));
  const loginMutation = useMutation({
    mutationFn: login,
    onSuccess: () => {
      window.location.href = normalizeRedirect(searchParams.get('redirect') || searchParams.get('next'));
    },
  });

  useEffect(() => {
    if (me.data?.id) {
      navigate(normalizeRedirect(searchParams.get('redirect') || searchParams.get('next')), { replace: true });
    }
  }, [me.data?.id, navigate, searchParams]);

  useEffect(() => {
    let markIndex = 0;
    const timer = window.setInterval(() => {
      markIndex = (markIndex + 1) % commandMarks.length;
      setCommandMark(commandMarks[markIndex]);
    }, 160);

    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let timer: number;

    const tick = () => {
      const command = commandSnippets[commandIndexRef.current];
      const deleting = commandDeletingRef.current;
      const nextCharIndex = commandCharIndexRef.current + (deleting ? -1 : 1);

      commandCharIndexRef.current = nextCharIndex;
      setCommandText(command.slice(0, nextCharIndex));

      if (!deleting && nextCharIndex === command.length) {
        commandDeletingRef.current = true;
        timer = window.setTimeout(tick, 1400);
        return;
      }

      if (deleting && nextCharIndex === 0) {
        commandDeletingRef.current = false;
        commandIndexRef.current = (commandIndexRef.current + 1) % commandSnippets.length;
        timer = window.setTimeout(tick, 360);
        return;
      }

      timer = window.setTimeout(tick, deleting ? 42 : 70 + ((nextCharIndex * 13) % 55));
    };

    timer = window.setTimeout(tick, 520);

    return () => window.clearTimeout(timer);
  }, []);

  const handleLogin: FormProps<FormValues>['onFinish'] = async (values) => {
    loginMutation.mutate(values);
  };

  const handleLoginFailed: FormProps<FormValues>['onFinishFailed'] = ({ errorFields }) => {
    const firstError = errorFields[0]?.errors[0];
    if (firstError) {
      message.error(firstError, 2);
    }
  };

  return (
    <>
      <EvoLoginGlobalStyle />
      <EvoLoginShell>
        <OrbitField />
        <EvoFrame>
          <EvoTopBar>
            <EvoLogoArea>
              <EvoBrandMark />
              <EvoBrandWord>
                <EvoBrandTitle>{PRODUCT_TITLE}</EvoBrandTitle>
                <EvoBrandSubTitle>{PRODUCT_TITLE_ZH}</EvoBrandSubTitle>
              </EvoBrandWord>
            </EvoLogoArea>
            <EvoTopEmpty />
            <EvoNav aria-label="主导航">
              <Link to="/login">产品</Link>
              <a href="/api-docs">文档</a>
              <Link to="/about">关于</Link>
            </EvoNav>
          </EvoTopBar>

          <EvoBody>
            <EvoHero>
              <EvoEyebrow>{PRODUCT_TITLE}</EvoEyebrow>
              <EvoHeroTitle>
                持续进化，
                <br />
                持续智能。
              </EvoHeroTitle>
              <EvoDivider />
              <EvoHeroText>
                让智能系统在迭代中不断学习、适应与成长<EvoCommandMark aria-hidden="true">{commandMark}</EvoCommandMark>
              </EvoHeroText>
              <EvoInlineCommand aria-label="实时命令输入">$ {commandText}</EvoInlineCommand>
            </EvoHero>

            <EvoCenterSpacer />

            <EvoCard>
              <EvoCardTitle>欢迎回来</EvoCardTitle>
              <EvoCardSubTitle>
                开启AI自进化时代<EvoCommandMark aria-hidden="true">{commandMark}</EvoCommandMark>
              </EvoCardSubTitle>

              <EvoFormWrapper>
                <Form<FormValues>
                  form={form}
                  initialValues={defaultFormValues}
                  requiredMark={false}
                  onFinish={handleLogin}
                  onFinishFailed={handleLoginFailed}
                >
                  <EvoField>
                    <EvoLabel>邮箱</EvoLabel>
                    <EvoControl>
                      <EmailIcon aria-hidden="true" />
                      <Form.Item
                        noStyle
                        name="username"
                        rules={[
                          { required: true, message: emailPleaseText },
                          { type: 'email', message: emailFormatPleaseText },
                        ]}
                      >
                        <input placeholder={emailText} autoComplete="email" />
                      </Form.Item>
                    </EvoControl>
                  </EvoField>

                  <EvoField>
                    <EvoLabel>密码</EvoLabel>
                    <EvoControl>
                      <PasswordIcon aria-hidden="true" />
                      <Form.Item
                        noStyle
                        name="password"
                        rules={[
                          { required: true, message: passwordPleaseText },
                          {
                            pattern: /^\S+$/,
                            message: passwordCannotContainsWhitespaceText,
                          },
                          {
                            min: 6,
                            message: passwordLengthErrorText,
                          },
                        ]}
                      >
                        <input
                          type={showPassword ? 'text' : 'password'}
                          placeholder={passwordText}
                          autoComplete="current-password"
                        />
                      </Form.Item>
                      <EvoPasswordToggle
                        type="button"
                        aria-label={showPassword ? '隐藏密码' : '显示密码'}
                        aria-pressed={showPassword}
                        onClick={() => setShowPassword((visible) => !visible)}
                      >
                        {showPassword ? <EyeInvisibleOutlined aria-hidden="true" /> : <EyeOutlined aria-hidden="true" />}
                      </EvoPasswordToggle>
                    </EvoControl>
                  </EvoField>

                  <EvoForget>
                    <Link to="/forgot-password">忘记密码？</Link>
                  </EvoForget>

                  <EvoLoginButton disabled={loginMutation.isPending} type="submit">
                    {loginText}
                  </EvoLoginButton>
                </Form>
              </EvoFormWrapper>

              <EvoRegisterText>
                还没有账号？
              </EvoRegisterText>
              <EvoRegisterButton to="/register" aria-label="注册账号">
                注册账号
              </EvoRegisterButton>
            </EvoCard>
          </EvoBody>

          <EvoKeywords>
            自学习 · 自适应 · 自进化<EvoCommandMark aria-hidden="true">{commandMark}</EvoCommandMark>
          </EvoKeywords>
        </EvoFrame>
      </EvoLoginShell>
    </>
  );
};

export default LoginPage;
