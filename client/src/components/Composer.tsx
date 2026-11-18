import { SendOutlined, StopOutlined } from "@ant-design/icons";
import { Button, Card, Flex, Form, Input, Space, Typography } from "antd";
import { useState } from "react";

const { TextArea } = Input;

type Props = {
  disabled?: boolean;
  inFlight: boolean;
  onSend: (text: string) => Promise<void>;
  onStop: () => void;
};

export function Composer({
  disabled = false,
  inFlight,
  onSend,
  onStop,
}: Props) {
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const busy = disabled || inFlight || submitting;

  async function submit() {
    const text = value.trim();
    if (!text || busy) {
      return;
    }

    setSubmitting(true);
    try {
      await onSend(text);
      setValue("");
    } catch {
      // 保留输入，方便用户重试；错误提示由上层渲染。
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="composer-card" variant="borderless">
      <Form
        onFinish={() => {
          void submit();
        }}
      >
        <div className="composer-shell">
          <TextArea
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void submit();
              }
            }}
            placeholder="输入您的问题，例如：订单1002 不想要了"
            maxLength={500}
            autoSize={{ minRows: 2, maxRows: 5 }}
            variant="borderless"
            disabled={inFlight || submitting}
            className="composer-input"
          />

          <Flex className="composer-footer" align="center" justify="space-between">
            <Typography.Text type="secondary" className="composer-hint">
              Enter 发送 · Shift + Enter 换行
            </Typography.Text>
            <Space size={8}>
              {inFlight ? (
                <Button
                  icon={<StopOutlined />}
                  onClick={onStop}
                  className="composer-stop"
                >
                  停止
                </Button>
              ) : (
                <Button
                  type="primary"
                  htmlType="submit"
                  icon={<SendOutlined />}
                  disabled={busy || !value.trim()}
                  className="composer-send"
                >
                  发送
                </Button>
              )}
            </Space>
          </Flex>
        </div>
      </Form>
    </Card>
  );
}
