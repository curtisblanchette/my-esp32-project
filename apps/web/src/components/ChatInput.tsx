import React, { useState, useCallback, useRef, useEffect } from "react";
import {
  sendChatStream,
  checkChatHealth,
  checkVoiceHealth,
  sendVoiceCommand,
  synthesizeSpeech,
  type ChatResponse,
  type StreamChatEvent,
  type VoiceHealthStatus,
} from "../api";

type ChatMessage = {
  id: number;
  role: "user" | "assistant";
  content: string;
  action?: ChatResponse["action"];
  error?: boolean;
  audioUrl?: string;
};

export function ChatInput(): React.ReactElement {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isHealthy, setIsHealthy] = useState<boolean | null>(null);
  const [voiceHealth, setVoiceHealth] = useState<VoiceHealthStatus | null>(null);
  const [streamingContent, setStreamingContent] = useState("");
  const [isExpanded, setIsExpanded] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [autoPlayAudio, setAutoPlayAudio] = useState(true);
  const abortControllerRef = useRef<AbortController | null>(null);
  const streamingContentRef = useRef("");
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Auto-scroll to bottom when messages change or during streaming
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingContent]);

  // Collapse when clicking outside
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsExpanded(false);
      }
    }

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Cleanup audio URLs on unmount
  useEffect(() => {
    return () => {
      messages.forEach((msg) => {
        if (msg.audioUrl) URL.revokeObjectURL(msg.audioUrl);
      });
    };
  }, []);

  const checkHealth = useCallback(async () => {
    const [chatHealthy, voice] = await Promise.all([
      checkChatHealth(),
      checkVoiceHealth(),
    ]);
    setIsHealthy(chatHealthy);
    setVoiceHealth(voice);
  }, []);

  useEffect(() => {
    checkHealth();
    const interval = setInterval(checkHealth, 30000);
    return () => clearInterval(interval);
  }, [checkHealth]);

  const playAudio = useCallback((audioUrl: string) => {
    if (audioRef.current) {
      audioRef.current.pause();
    }
    const audio = new Audio(audioUrl);
    audioRef.current = audio;
    audio.play().catch((err) => console.error("Audio playback failed:", err));
  }, []);

  const speakResponse = useCallback(async (text: string, messageId: number) => {
    if (!voiceHealth?.tts_available) return;

    try {
      const audioBlob = await synthesizeSpeech(text);
      const audioUrl = URL.createObjectURL(audioBlob);

      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === messageId ? { ...msg, audioUrl } : msg
        )
      );

      if (autoPlayAudio) {
        playAudio(audioUrl);
      }
    } catch (err) {
      console.error("Speech synthesis failed:", err);
    }
  }, [voiceHealth?.tts_available, autoPlayAudio, playAudio]);

  const handleVoiceInput = useCallback(async (audioBlob: Blob) => {
    setIsLoading(true);
    setIsExpanded(true);

    try {
      const result = await sendVoiceCommand(audioBlob);

      if (result.transcription) {
        const userMessage: ChatMessage = {
          id: Date.now(),
          role: "user",
          content: result.transcription,
        };
        setMessages((prev) => [...prev, userMessage]);
      }

      const assistantMessage: ChatMessage = {
        id: Date.now() + 1,
        role: "assistant",
        content: result.response || "I couldn't understand that.",
        error: !result.ok,
      };
      setMessages((prev) => [...prev, assistantMessage]);

      // Speak the response
      if (result.response) {
        speakResponse(result.response, assistantMessage.id);
      }
    } catch (err) {
      console.error("Voice command failed:", err);
      const errorMessage: ChatMessage = {
        id: Date.now() + 1,
        role: "assistant",
        content: "Voice processing failed. Please try again.",
        error: true,
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  }, [speakResponse]);

  const toggleRecording = useCallback(async () => {
    if (isRecording) {
      // Stop recording
      if (mediaRecorderRef.current) {
        mediaRecorderRef.current.stop();
        setIsRecording(false);
      }
      return;
    }

    // Start recording
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

      // Try different mime types for compatibility
      let mimeType = "audio/webm";
      if (!MediaRecorder.isTypeSupported(mimeType)) {
        mimeType = "audio/mp4";
        if (!MediaRecorder.isTypeSupported(mimeType)) {
          mimeType = "audio/ogg";
          if (!MediaRecorder.isTypeSupported(mimeType)) {
            mimeType = ""; // Let browser choose
          }
        }
      }

      const options = mimeType ? { mimeType } : undefined;
      const mediaRecorder = new MediaRecorder(stream, options);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
          audioChunksRef.current.push(e.data);
        }
      };

      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        const audioBlob = new Blob(audioChunksRef.current, { type: mimeType || "audio/webm" });
        await handleVoiceInput(audioBlob);
      };

      mediaRecorder.start();
      setIsRecording(true);
    } catch (err) {
      console.error("Failed to start recording:", err);
      alert("Microphone access denied. Please allow microphone access and try again.");
    }
  }, [isRecording, handleVoiceInput]);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const trimmed = input.trim();
      if (!trimmed || isLoading) return;

      // Cancel any existing request
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      abortControllerRef.current = new AbortController();

      const userMessage: ChatMessage = {
        id: Date.now(),
        role: "user",
        content: trimmed,
      };

      setMessages((prev) => [...prev, userMessage]);
      setInput("");
      setIsLoading(true);
      setStreamingContent("");
      streamingContentRef.current = "";
      setIsExpanded(true);

      try {
        let finalEvent: StreamChatEvent | null = null;

        for await (const event of sendChatStream(trimmed, abortControllerRef.current.signal)) {
          if (event.type === "token") {
            streamingContentRef.current += event.token;
            setStreamingContent((prev) => prev + event.token);
          } else if (event.type === "done" || event.type === "error") {
            finalEvent = event;
          }
        }

        // Create assistant message from final event
        if (finalEvent) {
          const assistantMessage: ChatMessage = {
            id: Date.now() + 1,
            role: "assistant",
            content: finalEvent.reply,
            action: finalEvent.type === "done" ? finalEvent.action : undefined,
            error: finalEvent.type === "error" || (finalEvent.type === "done" && !finalEvent.ok),
          };
          setMessages((prev) => [...prev, assistantMessage]);

          // Speak the response if auto-play is enabled
          if (autoPlayAudio && finalEvent.reply && voiceHealth?.tts_available) {
            speakResponse(finalEvent.reply, assistantMessage.id);
          }
        } else if (streamingContentRef.current) {
          // Stream ended without a done event - show what we received
          const assistantMessage: ChatMessage = {
            id: Date.now() + 1,
            role: "assistant",
            content: streamingContentRef.current + "\n\n(Response incomplete)",
            error: true,
          };
          setMessages((prev) => [...prev, assistantMessage]);
        } else {
          // No content at all
          const assistantMessage: ChatMessage = {
            id: Date.now() + 1,
            role: "assistant",
            content: "The response was incomplete. Please try again.",
            error: true,
          };
          setMessages((prev) => [...prev, assistantMessage]);
        }
      } catch (err) {
        // Only show error if not aborted
        if (err instanceof Error && err.name !== "AbortError") {
          const errorMessage: ChatMessage = {
            id: Date.now() + 1,
            role: "assistant",
            content: "Failed to connect to the assistant. Please try again.",
            error: true,
          };
          setMessages((prev) => [...prev, errorMessage]);
        }
      } finally {
        setIsLoading(false);
        setStreamingContent("");
        abortControllerRef.current = null;
      }
    },
    [input, isLoading, autoPlayAudio, voiceHealth?.tts_available, speakResponse]
  );

  const formatAction = (action: ChatResponse["action"]): string | null => {
    if (!action) return null;
    if (action.type === "command" && action.target) {
      const value = action.value === true ? "ON" : action.value === false ? "OFF" : String(action.value);
      return `${action.target} → ${value}`;
    }
    if (action.type === "query" && action.sensor) {
      return `${action.sensor}: ${action.value ?? "N/A"}`;
    }
    return null;
  };

  // Parse markdown-style formatting in messages
  const formatMessage = (content: string): React.ReactNode => {
    const lines = content.split("\n");
    return lines.map((line, i) => {
      // Parse **bold** markers
      const parts: React.ReactNode[] = [];
      let remaining = line;
      let partIndex = 0;

      while (remaining.includes("**")) {
        const start = remaining.indexOf("**");
        if (start > 0) {
          parts.push(remaining.slice(0, start));
        }
        remaining = remaining.slice(start + 2);
        const end = remaining.indexOf("**");
        if (end === -1) {
          parts.push("**" + remaining);
          remaining = "";
          break;
        }
        parts.push(<strong key={`${i}-${partIndex++}`}>{remaining.slice(0, end)}</strong>);
        remaining = remaining.slice(end + 2);
      }
      if (remaining) parts.push(remaining);

      // Detect line types for styling
      const isHeader = line.match(/^[📊🌡️💧⚠️✓]/);
      const isBullet = line.trim().startsWith("•") || line.trim().startsWith("→");

      return (
        <div
          key={i}
          className={`${isHeader ? "font-medium mt-2 first:mt-0" : ""} ${isBullet ? "pl-2 text-[0.8125rem] opacity-90" : ""}`}
        >
          {parts.length > 0 ? parts : line || "\u00A0"}
        </div>
      );
    });
  };

  const voiceAvailable = voiceHealth?.stt_available && voiceHealth?.tts_available;

  return (
    <div ref={containerRef} className="border border-panel-border rounded-xl p-4 backdrop-blur-[10px]">
      <div
        className="flex items-center justify-between mb-3 cursor-pointer"
        onClick={() => setIsExpanded((prev) => !prev)}
      >
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-medium opacity-80">Assistant</h2>
          {messages.length > 0 && (
            <span className="text-xs opacity-50">
              {messages.length} message{messages.length !== 1 ? "s" : ""}
              {!isExpanded && " — click to expand"}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {/* Audio auto-play toggle */}
          {voiceHealth?.tts_available && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                setAutoPlayAudio((prev) => !prev);
              }}
              className={`p-1 rounded transition-colors ${autoPlayAudio ? "text-blue-400" : "text-gray-500"}`}
              title={autoPlayAudio ? "Auto-play enabled" : "Auto-play disabled"}
            >
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                {autoPlayAudio ? (
                  <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z" />
                ) : (
                  <path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z" />
                )}
              </svg>
            </button>
          )}
          {messages.length > 0 && (
            <svg
              className={`w-4 h-4 opacity-50 transition-transform ${isExpanded ? "rotate-180" : ""}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
            </svg>
          )}
          <div className="flex items-center gap-1.5 text-xs">
            <div
              className={`w-2 h-2 rounded-full ${
                isHealthy === null ? "bg-gray-400" : isHealthy ? "bg-green-500" : "bg-red-500"
              }`}
            />
            <span className="opacity-60">
              {isHealthy === null ? "Checking..." : isHealthy ? "AI Ready" : "AI Offline"}
            </span>
            {voiceAvailable && (
              <>
                <div className="w-2 h-2 rounded-full bg-purple-500 ml-2" />
                <span className="opacity-60">Voice</span>
              </>
            )}
          </div>
        </div>
      </div>

      {messages.length > 0 && isExpanded && (
        <div className="max-h-48 overflow-y-auto mb-3 space-y-2">
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`text-sm ${msg.role === "user" ? "text-right" : "text-left"}`}
            >
              <span
                className={`inline-block px-3 py-2 rounded-lg max-w-[85%] text-left ${
                  msg.role === "user"
                    ? "bg-blue-500/20 text-blue-200"
                    : msg.error
                      ? "bg-red-500/20 text-red-200"
                      : "bg-white/10"
                }`}
              >
                {msg.role === "assistant" ? formatMessage(msg.content) : msg.content}
                {msg.action && (
                  <span className="block text-xs opacity-70 mt-1">
                    {msg.action.type === "command" && "✓ "}
                    {formatAction(msg.action)}
                  </span>
                )}
                {/* Play audio button for assistant messages */}
                {msg.role === "assistant" && msg.audioUrl && (
                  <button
                    onClick={() => playAudio(msg.audioUrl!)}
                    className="inline-flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 mt-1"
                  >
                    <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 24 24">
                      <path d="M8 5v14l11-7z" />
                    </svg>
                    Play
                  </button>
                )}
              </span>
            </div>
          ))}
          {isLoading && (
            <div className="text-left">
              <span className="inline-block px-3 py-2 rounded-lg bg-white/10 text-sm max-w-[85%]">
                {streamingContent ? (
                  <span>{formatMessage(streamingContent)}<span className="animate-pulse">▋</span></span>
                ) : (
                  <span className="animate-pulse">
                    {isRecording ? "Recording..." : "Thinking..."}
                  </span>
                )}
              </span>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      )}

      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onFocus={() => messages.length > 0 && setIsExpanded(true)}
          placeholder="Ask your assistant..."
          disabled={isLoading || isHealthy === false}
          className="flex-1 px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-sm placeholder:opacity-50 focus:outline-none focus:border-blue-500/50 disabled:opacity-50"
        />

        {/* Voice input button */}
        {voiceAvailable && (
          <button
            type="button"
            onClick={toggleRecording}
            disabled={isLoading && !isRecording}
            className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
              isRecording
                ? "bg-red-500/40 border border-red-500/50 animate-pulse"
                : "bg-purple-500/20 hover:bg-purple-500/30 border border-purple-500/30"
            } disabled:opacity-50 disabled:cursor-not-allowed`}
            title={isRecording ? "Click to stop" : "Click to speak"}
          >
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
              {isRecording ? (
                <path d="M6 6h12v12H6z" />
              ) : (
                <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.91-3c-.49 0-.9.36-.98.85C16.52 14.2 14.47 16 12 16s-4.52-1.8-4.93-4.15c-.08-.49-.49-.85-.98-.85-.61 0-1.09.54-1 1.14.49 3 2.89 5.35 5.91 5.78V20c0 .55.45 1 1 1s1-.45 1-1v-2.08c3.02-.43 5.42-2.78 5.91-5.78.1-.6-.39-1.14-1-1.14z" />
              )}
            </svg>
          </button>
        )}

        <button
          type="submit"
          disabled={isLoading || !input.trim() || isHealthy === false}
          className="px-4 py-2 bg-blue-500/20 hover:bg-blue-500/30 border border-blue-500/30 rounded-lg text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          Send
        </button>
      </form>
    </div>
  );
}
