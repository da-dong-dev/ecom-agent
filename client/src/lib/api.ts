/** 业务 JSON 接口：只调 base.request，不直接 fetch。 */
import { request } from "./base";

export type ConversationSummary = {
  id: number;
  created_at: string;
  updated_at: string;
  title: string;
  active_flow: string | null;
  pending_question: string | null;
};

export type ConversationList = {
  items: ConversationSummary[];
};

export type ChatMessage = {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  status?: string | null;
  client_message_id?: string | null;
  reply_to_client_message_id?: string | null;
  meta?: Record<string, unknown> | null;
};

export type ConversationHistory = {
  conversation_id: number;
  active_flow: string | null;
  pending_question: string | null;
  items: ChatMessage[];
};

export async function listConversations(): Promise<ConversationList> {
  const response = await request("/api/conversations");
  return (await response.json()) as ConversationList;
}

export async function getMessages(cid: number): Promise<ConversationHistory> {
  const response = await request(`/api/conversations/${cid}/messages`);
  return (await response.json()) as ConversationHistory;
}
