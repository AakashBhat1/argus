"use client";

import { useEffect, useRef, useState } from "react";
import { Video } from "lucide-react";
import { CSRF_HEADERS, ensureFreshSession } from "@/lib/session";

interface Props {
  cameraId: string;
  /** Which service's media server carries this camera. */
  service?: "surveillance" | "parking";
  className?: string;
}

const SURVEILLANCE_MEDIA_BASE = process.env.NEXT_PUBLIC_MEDIAMTX_URL || "http://localhost:8889";
// Gate/lot cameras are ingested by the parking host's own MediaMTX.
const PARKING_MEDIA_BASE = process.env.NEXT_PUBLIC_PARKING_MEDIAMTX_URL || SURVEILLANCE_MEDIA_BASE;

export default function WebRTCPlayer({ cameraId, service = "surveillance", className = "" }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const startWebRTC = async () => {
      try {
        pcRef.current?.close();
        const pc = new RTCPeerConnection();
        pcRef.current = pc;

        pc.addTransceiver("video", { direction: "recvonly" });

        pc.ontrack = (event) => {
          if (videoRef.current && event.streams[0]) {
            videoRef.current.srcObject = event.streams[0];
          }
        };

        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        // MediaMTX WHEP endpoint (path uses camera_id, not camera_name).
        // The edge authorises playback with the session cookie first.
        await ensureFreshSession();
        const base = service === "parking" ? PARKING_MEDIA_BASE : SURVEILLANCE_MEDIA_BASE;
        const response = await fetch(`${base.replace(/\/$/, "")}/${cameraId}/whep`, {
          method: "POST",
          credentials: "include",
          headers: {
            ...CSRF_HEADERS,
            "Content-Type": "application/sdp",
          },
          body: offer.sdp,
        });

        if (!response.ok) {
          throw new Error("Stream not available yet");
        }

        const answerSdp = await response.text();
        if (active) {
          await pc.setRemoteDescription(new RTCSessionDescription({ type: "answer", sdp: answerSdp }));
          setError(null);
        }
      } catch (err: unknown) {
        if (active) {
          setError(
            err instanceof Error ? err.message : "Failed to connect to stream"
          );
          // Retry connection after 3 seconds if stream isn't up
          retryTimer = setTimeout(startWebRTC, 3000);
        }
      }
    };

    startWebRTC();

    return () => {
      active = false;
      if (retryTimer) clearTimeout(retryTimer);
      if (pcRef.current) {
        pcRef.current.close();
      }
    };
  }, [cameraId, service]);

  return (
    <div className={`relative bg-black w-full h-full overflow-hidden ${className}`}>
      {error && !videoRef.current?.srcObject && (
        <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-500 z-10">
          <Video className="w-12 h-12 mb-3 opacity-30 animate-pulse" />
          <p className="text-sm">Connecting to stream...</p>
        </div>
      )}
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        className="absolute inset-0 w-full h-full object-contain"
      />
    </div>
  );
}
