import { Button, Card, Flex, Form, Input, Space } from "antd";
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
        <Flex className="composer" gap={10} align="flex-end">
          <TextArea
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void submit();
              }
            }}
            placeholder="输入您的问题，Enter 发送，Shift+Enter 换行"
            maxLength={500}
            showCount
            autoSize={{ minRows: 2, maxRows: 6 }}
            disabled={inFlight || submitting}
          />

          <Space className="composer-actions">
            {inFlight ? (
              <Button onClick={onStop}>停止</Button>
            ) : (
              <Button
                type="primary"
                htmlType="submit"
                disabled={busy || !value.trim()}
              >
                发送
              </Button>
            )}
          </Space>
        </Flex>
      </Form>
    </Card>
  );
}
