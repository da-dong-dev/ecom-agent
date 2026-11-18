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

export async function deleteConversation(cid: number): Promise<void> {
  await request(`/api/conversations/${cid}`, { method: "DELETE" });
}

// ── Ch09 审核 ────────────────────────────────────────────────

export type ReviewStatus =
  | "pending"
  | "merged"
  | "approved"
  | "written_back"
  | "rejected";

export type ReviewItem = {
  id: number;
  question: string;
  normalized: string | null;
  suggested_answer: string | null;
  occurrence: number;
  status: ReviewStatus;
  source: string | null;
  reason: string | null;
  created_at: string;
  reviewed_at: string | null;
};

export type RetrievedHit = {
  id: string;
  similarity: number | null;
  section: string;
  text: string;
  source: string;
};

export type MergedQuestion = {
  id: number;
  question: string;
  source: string | null;
  reason: string | null;
  retrieved: RetrievedHit[] | null;
  created_at: string;
};

export type ReviewDetail = ReviewItem & {
  retrieved: RetrievedHit[] | null;
  merged_questions: MergedQuestion[];
};

export type ReviewQueue = {
  items: ReviewItem[];
};

export type ReviewActionResult = {
  id: number;
  status: ReviewStatus;
  faq_id?: number;
  question?: string;
  answer?: string;
  kb_ref?: string | null;
  kb_version?: string;
  reason?: string | null;
};

export async function listReviewQueue(
  status: ReviewStatus = "pending",
): Promise<ReviewQueue> {
  const response = await request(
    `/api/review/queue?status=${encodeURIComponent(status)}`,
  );
  return (await response.json()) as ReviewQueue;
}

export async function getReviewDetail(id: number): Promise<ReviewDetail> {
  const response = await request(`/api/review/${id}`);
  return (await response.json()) as ReviewDetail;
}

export async function approveReview(
  id: number,
  answer: string,
): Promise<ReviewActionResult> {
  const response = await request(`/api/review/${id}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  });
  return (await response.json()) as ReviewActionResult;
}

export async function rejectReview(
  id: number,
  reason: string,
): Promise<ReviewActionResult> {
  const response = await request(`/api/review/${id}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason || null }),
  });
  return (await response.json()) as ReviewActionResult;
}

export async function retryReview(id: number): Promise<ReviewActionResult> {
  const response = await request(`/api/review/${id}/retry`, {
    method: "POST",
  });
  return (await response.json()) as ReviewActionResult;
}

// ── Ch09 用户反馈 ────────────────────────────────────────────

export type FeedbackRating = "up" | "down";

export type FeedbackResult = {
  ok: boolean;
  recorded: boolean;
  id?: number;
};

export async function sendFeedback(input: {
  conversation_id: number;
  message_id: number;
  rating: FeedbackRating;
  question?: string;
}): Promise<FeedbackResult> {
  const response = await request("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return (await response.json()) as FeedbackResult;
}
