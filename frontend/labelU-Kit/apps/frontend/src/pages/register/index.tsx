import type { FormProps } from 'antd';
import { Form } from 'antd';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { i18n } from '@labelu/i18n';
import { useEffect, useRef, useState } from 'react';

import { signUp } from '@/api/services/user';
import { ReactComponent as EmailIcon } from '@/assets/svg/email.svg';
import { ReactComponent as PasswordIcon } from '@/assets/svg/password.svg';
import { message } from '@/StaticAnt';
import { PRODUCT_TITLE, PRODUCT_TITLE_ZH } from '@/utils/product';

import { OrbitField } from '../login/OrbitField';
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
  EvoTopBar,
  EvoTopEmpty,
} from '../login/style';

interface FormValues {
  username: string;
  password: string;
  confirmPassword: string;
}

const commandSnippets = ['signup --email --secure', 'init account --evoai', 'verify identity --adaptive'];
const commandMarks = ['_', '\\', '|', '/'];
const defaultFormValues: FormValues = {
  username: '',
  password: '',
  confirmPassword: '',
};

const SignUpPage = () => {
  const [commandText, setCommandText] = useState('');
  const [commandMark, setCommandMark] = useState(commandMarks[0]);
  const commandIndexRef = useRef(0);
  const commandCharIndexRef = useRef(0);
  const commandDeletingRef = useRef(false);
  const [form] = Form.useForm<FormValues>();
  const navigate = useNavigate();
  const signUpText = String(i18n.t('signUp'));
  const loginText = String(i18n.t('login'));
  const emailText = String(i18n.t('email'));
  const passwordText = String(i18n.t('password'));
  const passwordConfirmText = String(i18n.t('passwordConfirm'));
  const emailPleaseText = String(i18n.t('emailPlease'));
  const emailFormatPleaseText = String(i18n.t('emailFormatPlease'));
  const passwordPleaseText = String(i18n.t('passwordPlease'));
  const passwordCannotContainsWhitespaceText = String(i18n.t('passwordCannotContainsWhitespace'));
  const passwordLengthErrorText = String(i18n.t('passwordLengthError'));
  const confirmPasswordPleaseText = String(i18n.t('confirmPasswordPlease'));
  const passwordRepeatedUnMatchText = String(i18n.t('passwordRepeatedUnMatch'));
  const signUpSuccessText = String(i18n.t('signUpSuccess'));
  const signUpFailedText = String(i18n.t('signUpFailed'));
  const signUpMutation = useMutation({
    mutationFn: signUp,
    onSuccess: () => {
      message.success(signUpSuccessText);
      navigate('/login');
    },
    onError: (error: any) => {
      const errorCode = error?.response?.data?.err_code;
      message.error(errorCode === 40001 ? '该邮箱已注册，请直接登录' : signUpFailedText);
    },
  });

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

  const handleSignUp: FormProps<FormValues>['onFinish'] = async ({ username, password }) => {
    signUpMutation.mutate({ username, password });
  };

  const handleSignUpFailed: FormProps<FormValues>['onFinishFailed'] = ({ errorFields }) => {
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
                创建账号，让智能系统从第一步开始持续学习<EvoCommandMark aria-hidden="true">{commandMark}</EvoCommandMark>
              </EvoHeroText>
              <EvoInlineCommand aria-label="实时命令输入">$ {commandText}</EvoInlineCommand>
            </EvoHero>

            <EvoCenterSpacer />

            <EvoCard>
              <EvoCardTitle>创建账号</EvoCardTitle>
              <EvoCardSubTitle>
                加入AI自进化时代<EvoCommandMark aria-hidden="true">{commandMark}</EvoCommandMark>
              </EvoCardSubTitle>

              <EvoFormWrapper>
                <Form<FormValues>
                  form={form}
                  initialValues={defaultFormValues}
                  requiredMark={false}
                  onFinish={handleSignUp}
                  onFinishFailed={handleSignUpFailed}
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
                        <input type="password" placeholder={passwordText} autoComplete="new-password" />
                      </Form.Item>
                    </EvoControl>
                  </EvoField>

                  <EvoField>
                    <EvoLabel>确认密码</EvoLabel>
                    <EvoControl>
                      <PasswordIcon aria-hidden="true" />
                      <Form.Item
                        noStyle
                        name="confirmPassword"
                        dependencies={['password']}
                        rules={[
                          {
                            required: true,
                            message: confirmPasswordPleaseText,
                          },
                          ({ getFieldValue }) => ({
                            validator(_unused, value) {
                              if (!value || getFieldValue('password') === value) {
                                return Promise.resolve();
                              }

                              return Promise.reject(new Error(passwordRepeatedUnMatchText));
                            },
                          }),
                          {
                            min: 6,
                            message: passwordLengthErrorText,
                          },
                        ]}
                      >
                        <input type="password" placeholder={passwordConfirmText} autoComplete="new-password" />
                      </Form.Item>
                    </EvoControl>
                  </EvoField>

                  <EvoLoginButton disabled={signUpMutation.isPending} type="submit">
                    {signUpText}
                  </EvoLoginButton>
                </Form>
              </EvoFormWrapper>

              <EvoRegisterText>
                已有账号？ <Link to="/login">{loginText}</Link>
              </EvoRegisterText>
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
export default SignUpPage;
