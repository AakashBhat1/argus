"use client";

import { useState, useRef, useEffect } from "react";
import { Send, Shield, Sparkles, User, CheckCircle, AlertTriangle } from "lucide-react";
import { api, type ParkingChatResponse } from "@/lib/api";

export interface ChatMessage {
  id: string;
  sender: "user" | "bot";
  text: string;
  timestamp: string;
  isCommand?: boolean;
  executed?: boolean;
  error?: string | null;
  commandDetail?: Record<string, any> | null;
}

interface ParkBotChatProps {
  isAdmin: boolean;
  onCommandExecuted: (command: Record<string, any>) => void;
  addLog: (type: "info" | "success" | "warn" | "system", message: string) => void;
}

export default function ParkBotChat({ isAdmin, onCommandExecuted, addLog }: ParkBotChatProps) {
  const [chatInput, setChatInput] = useState("");
  const [chatMode, setChatMode] = useState<"read" | "command">("read");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatLoading, setChatLoading] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // Default mode & welcome message
  useEffect(() => {
    if (isAdmin) {
      setChatMode("command");
    }
    const welcomeMsg: ChatMessage = {
      id: "welcome",
      sender: "bot",
      text: "Hello! I am ParkBot. I can assist you with looking up space occupancy, finding vehicle locations, or releasing slots. Ask me anything about parking.",
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    };
    setChatMessages([welcomeMsg]);
  }, [isAdmin]);

  // Scroll to bottom
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!chatInput.trim() || chatLoading) return;

    const text = chatInput.trim();
    setChatInput("");
    setChatLoading(true);

    const timeStr = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const userMsg: ChatMessage = {
      id: Math.random().toString(),
      sender: "user",
      text,
      timestamp: timeStr,
      isCommand: chatMode === "command",
    };

    setChatMessages((prev) => [...prev, userMsg]);

    try {
      let response: ParkingChatResponse;
      if (chatMode === "command" && isAdmin) {
        response = await api.parking.chatCommand(text);
      } else {
        response = await api.parking.chat(text, chatMode === "command");
      }

      const botMsg: ChatMessage = {
        id: Math.random().toString(),
        sender: "bot",
        text: response.content,
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        isCommand: response.mode === "command",
        executed: response.executed,
        error: response.error,
        commandDetail: response.command,
      };

      setChatMessages((prev) => [...prev, botMsg]);

      if (response.executed && response.command) {
        onCommandExecuted(response.command);
      }
    } catch (err) {
      console.error("ParkBot error:", err);
      const errorMsg: ChatMessage = {
        id: Math.random().toString(),
        sender: "bot",
        text: "Sorry, I encountered an error processing your message. Make sure the backend service is available.",
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      };
      setChatMessages((prev) => [...prev, errorMsg]);
    } finally {
      setChatLoading(false);
    }
  };

  return (
    <div className="card flex flex-col h-[560px] !p-0">
      {/* Chat Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800/40 bg-slate-900/20">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500/20 to-violet-500/20 border border-indigo-500/20 flex items-center justify-center shadow">
            <Sparkles className="w-4.5 h-4.5 text-indigo-400" />
          </div>
          <div>
            <h3 className="text-xs font-bold text-slate-200">ParkBot AI</h3>
            <p className="text-[10px] text-slate-500">Command & Assistant Copilot</p>
          </div>
        </div>

        {/* Chat Mode Selector */}
        <div className="flex items-center gap-1.5 bg-slate-950 p-1 rounded-lg border border-slate-850">
          <button
            onClick={() => setChatMode("read")}
            className={`text-[9px] font-bold px-2 py-1 rounded transition-colors uppercase tracking-wider ${
              chatMode === "read"
                ? "bg-slate-800 text-slate-200"
                : "text-slate-500 hover:text-slate-300"
            }`}
          >
            Assist
          </button>
          <button
            onClick={() => isAdmin && setChatMode("command")}
            disabled={!isAdmin}
            className={`text-[9px] font-bold px-2 py-1 rounded transition-colors uppercase tracking-wider flex items-center gap-1 ${
              chatMode === "command"
                ? "bg-indigo-600 text-white"
                : "text-slate-500 hover:text-slate-300 disabled:opacity-40"
            }`}
            title={!isAdmin ? "Requires admin privileges" : "Toggle command execution"}
          >
            <Shield className="w-2.5 h-2.5" /> Command
          </button>
        </div>
      </div>

      {/* Chat Messages Panel */}
      <div className="flex-1 overflow-y-auto p-5 space-y-4 max-h-[420px]">
        {chatMessages.map((msg) => {
          const isBot = msg.sender === "bot";
          return (
            <div
              key={msg.id}
              className={`flex gap-3 max-w-[85%] ${isBot ? "self-start" : "self-end ml-auto flex-row-reverse"}`}
            >
              {isBot ? (
                <div className="w-6 h-6 rounded-full bg-indigo-500/10 border border-indigo-500/25 flex items-center justify-center shrink-0">
                  <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
                </div>
              ) : (
                <div className="w-6 h-6 rounded-full bg-blue-500/10 border border-blue-500/25 flex items-center justify-center shrink-0">
                  <User className="w-3.5 h-3.5 text-blue-400" />
                </div>
              )}

              <div className="space-y-1">
                <div
                  className={`rounded-2xl px-3.5 py-2.5 text-xs shadow-sm ${
                    isBot
                      ? "bg-slate-800/60 border border-slate-700/20 text-slate-200 rounded-tl-none"
                      : "bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 rounded-tr-none"
                  }`}
                >
                  <p className="leading-relaxed whitespace-pre-wrap">{msg.text}</p>

                  {/* If bot response executed a command successfully */}
                  {isBot && msg.executed && msg.commandDetail && (
                    <div className="mt-2.5 pt-2.5 border-t border-slate-700/40 space-y-1.5">
                      <span className="text-[10px] text-emerald-400 font-bold uppercase tracking-wider flex items-center gap-1">
                        <CheckCircle className="w-3 h-3" /> Command Executed
                      </span>
                      <div className="bg-slate-950/60 rounded-lg p-2 font-mono text-[9px] text-slate-400 border border-slate-800/40">
                        <span className="text-slate-500">Action:</span> {msg.commandDetail.action}
                        {Object.entries(msg.commandDetail)
                          .filter(([k]) => k !== "action")
                          .map(([k, v]) => (
                            <div key={k}>
                              <span className="text-slate-500">{k}:</span> {String(v)}
                            </div>
                          ))}
                      </div>
                    </div>
                  )}

                  {/* If bot command failed */}
                  {isBot && msg.error && (
                    <div className="mt-2 pt-2 border-t border-slate-700/40 space-y-1">
                      <span className="text-[10px] text-red-400 font-bold uppercase tracking-wider flex items-center gap-1">
                        <AlertTriangle className="w-3 h-3" /> Execution Failed
                      </span>
                      <p className="text-[10px] text-slate-400 italic">{msg.error}</p>
                    </div>
                  )}
                </div>
                <p className={`text-[9px] text-slate-500 font-mono ${isBot ? "text-left" : "text-right"}`}>
                  {msg.timestamp} {msg.isCommand && <span className="text-[8px] bg-slate-800 text-indigo-400 px-1 rounded uppercase font-bold">CMD</span>}
                </p>
              </div>
            </div>
          );
        })}
        {chatLoading && (
          <div className="flex gap-3 max-w-[85%] self-start">
            <div className="w-6 h-6 rounded-full bg-indigo-500/10 border border-indigo-500/25 flex items-center justify-center shrink-0">
              <Sparkles className="w-3.5 h-3.5 text-indigo-400 animate-spin" />
            </div>
            <div className="bg-slate-800/30 border border-slate-700/10 rounded-2xl rounded-tl-none px-3.5 py-2.5 text-xs text-slate-400 flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-slate-500 animate-bounce" style={{ animationDelay: "0ms" }} />
              <span className="w-1.5 h-1.5 rounded-full bg-slate-500 animate-bounce" style={{ animationDelay: "150ms" }} />
              <span className="w-1.5 h-1.5 rounded-full bg-slate-500 animate-bounce" style={{ animationDelay: "300ms" }} />
            </div>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>

      {/* Chat Input */}
      <form
        onSubmit={handleSendMessage}
        className="p-4 border-t border-slate-800/40 bg-slate-900/10 flex gap-2.5 items-center"
      >
        <input
          type="text"
          value={chatInput}
          onChange={(e) => setChatInput(e.target.value)}
          placeholder={
            chatMode === "command"
              ? "Type command to execute (e.g. 'release space G-01')"
              : "Ask ParkBot a question..."
          }
          className="input !py-2 !text-xs !bg-slate-950/60"
          disabled={chatLoading}
        />
        <button
          type="submit"
          disabled={chatLoading || !chatInput.trim()}
          className="btn-gradient !p-2 !rounded-xl disabled:opacity-40 disabled:scale-100 flex items-center justify-center w-9 h-9 shrink-0"
          aria-label="Send message"
        >
          <Send className="w-4 h-4 text-white" />
        </button>
      </form>
    </div>
  );
}
