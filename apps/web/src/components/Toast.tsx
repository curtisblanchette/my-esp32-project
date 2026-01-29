import React, { useEffect } from "react";

export interface ToastProps {
  message: string;
  type?: "error" | "warning" | "success" | "info";
  duration?: number;
  onClose: () => void;
}

export function Toast(props: ToastProps): React.ReactElement {
  const type = props.type ?? "info";
  const duration = props.duration ?? 4000;

  useEffect(() => {
    const timer = setTimeout(() => {
      props.onClose();
    }, duration);

    return () => clearTimeout(timer);
  }, [duration, props.onClose]);

  const colors = {
    error: "bg-red-500/10 border-red-500/30 text-red-600 dark:text-red-400",
    warning: "bg-yellow-500/10 border-yellow-500/30 text-yellow-600 dark:text-yellow-400",
    success: "bg-green-500/10 border-green-500/30 text-green-600 dark:text-green-400",
    info: "bg-blue-500/10 border-blue-500/30 text-blue-600 dark:text-blue-400",
  };

  const icons = {
    error: (
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
    ),
    warning: (
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
    ),
    success: (
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
    ),
    info: (
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    ),
  };

  return (
    <div className={`flex items-start gap-2 px-4 py-3 rounded-lg border ${colors[type]} shadow-lg animate-slide-in-down`}>
      <svg className="w-5 h-5 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        {icons[type]}
      </svg>
      <div className="flex-1 text-sm">{props.message}</div>
      <button
        onClick={props.onClose}
        className="flex-shrink-0 opacity-60 hover:opacity-100 transition-opacity"
        aria-label="Close"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
}
