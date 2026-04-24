"use client";

import { useState, useRef, useEffect, FormEvent } from "react";
import { MessageCircle, X, Send, Bot } from "lucide-react";

const CHATBOT_ENDPOINT = (
  process.env.NEXT_PUBLIC_CHATBOT_URL || "/chat"
).replace(/\/$/, "");

type Message = {
  id: string;
  from: "user" | "bot";
  text: string;
};

export default function Chatbot() {
  const [isOpen, setIsOpen] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "welcome",
      from: "bot",
      text: "Hi, I'm your SentinelAI assistant. Ask me about system status, alerts, or camera feeds.",
    },
  ]);
  const [isSending, setIsSending] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, isOpen]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || isSending) return;

    const userMessage: Message = {
      id: `${Date.now()}-user`,
      from: "user",
      text: trimmed,
    };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsSending(true);

    try {
      const res = await fetch(CHATBOT_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: trimmed }),
      });

      if (!res.ok) {
        throw new Error(`API error: ${res.status}`);
      }

      const data: { reply?: string } = await res.json();
      const replyText =
        data.reply ||
        "Sorry, I couldn't understand the response from the server.";

      const botMessage: Message = {
        id: `${Date.now()}-bot`,
        from: "bot",
        text: replyText,
      };
      setMessages((prev) => [...prev, botMessage]);
    } catch {
      const botMessage: Message = {
        id: `${Date.now()}-error`,
        from: "bot",
        text: "Sorry, I couldn't reach the chatbot service. Please check the chatbot URL and service status.",
      };
      setMessages((prev) => [...prev, botMessage]);
    } finally {
      setIsSending(false);
    }
  }

  return (
    <div className="fixed bottom-5 right-5 z-50">
      {!isOpen && (
        <button
          type="button"
          onClick={() => setIsOpen(true)}
          className="group rounded-full bg-gradient-to-r from-blue-600 to-violet-600 hover:from-blue-500 hover:to-violet-500 text-white px-5 py-3 shadow-lg shadow-blue-500/25 hover:shadow-blue-500/40 hover:shadow-xl text-sm font-semibold transition-all duration-300 flex items-center gap-2.5 active:scale-95 hover:scale-105"
          aria-label="Open chat assistant"
        >
          <MessageCircle className="w-4 h-4" />
          Chat with AI
        </button>
      )}

      {isOpen && (
        <div className="w-80 sm:w-96 bg-slate-900/95 border border-slate-700/50 rounded-2xl shadow-panel backdrop-blur-md animate-slide-up flex flex-col overflow-hidden">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800/60 bg-slate-950/60">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500/20 to-violet-500/20 flex items-center justify-center">
                <Bot className="w-4 h-4 text-blue-400" />
              </div>
              <div>
                <p className="text-xs font-semibold text-slate-200">
                  SentinelAI Assistant
                </p>
                <p className="text-[10px] text-slate-500">
                  Status, alerts & cameras
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setIsOpen(false)}
              className="text-slate-500 hover:text-slate-300 p-1.5 rounded-lg hover:bg-slate-800/60 transition-colors"
              aria-label="Close chat"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Messages */}
          <div className="flex-1 px-3 py-3 space-y-2.5 overflow-y-auto max-h-80 text-xs">
            {messages.map((m) => (
              <div
                key={m.id}
                className={`flex ${
                  m.from === "user" ? "justify-end" : "justify-start"
                }`}
              >
                <div
                  className={`max-w-[80%] rounded-2xl px-3.5 py-2.5 leading-relaxed ${
                    m.from === "user"
                      ? "bg-gradient-to-r from-blue-600 to-blue-500 text-white rounded-br-md"
                      : "bg-slate-800/60 text-slate-200 border border-slate-700/40 rounded-bl-md"
                  }`}
                >
                  {m.text}
                </div>
              </div>
            ))}
            {isSending && (
              <div className="flex justify-start">
                <div className="bg-slate-800/60 border border-slate-700/40 rounded-2xl rounded-bl-md px-4 py-3">
                  <div className="flex gap-1">
                    <span className="w-1.5 h-1.5 bg-slate-500 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                    <span className="w-1.5 h-1.5 bg-slate-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                    <span className="w-1.5 h-1.5 bg-slate-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                  </div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Input */}
          <form
            onSubmit={handleSubmit}
            className="border-t border-slate-800/60 bg-slate-950/60 px-3 py-2.5 flex items-center gap-2"
          >
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask a question..."
              className="flex-1 bg-slate-800/40 border border-slate-700/40 rounded-xl px-3.5 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-blue-500/40 focus:border-blue-500/30 transition-all"
            />
            <button
              type="submit"
              disabled={isSending || !input.trim()}
              className="bg-blue-500 hover:bg-blue-400 disabled:bg-slate-700 disabled:text-slate-500 text-white text-xs font-semibold p-2.5 rounded-xl transition-all duration-200 active:scale-95"
              aria-label="Send message"
            >
              <Send className="w-3.5 h-3.5" />
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
