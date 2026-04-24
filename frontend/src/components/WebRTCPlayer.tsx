"use client";

import { useEffect, useRef, useState } from "react";
import { Video } from "lucide-react";

interface Props {
  cameraId: string;
  className?: string;
}

export default function WebRTCPlayer({ cameraId, className = "" }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    const startWebRTC = async () => {
      try {
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

        // MediaMTX WHEP endpoint (path uses camera_id, not camera_name)
        const base = process.env.NEXT_PUBLIC_MEDIAMTX_URL || "http://localhost:8889";
        const response = await fetch(`${base.replace(/\/$/, "")}/${cameraId}/whep`, {
          method: "POST",
          headers: {
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
        }
      } catch (err: unknown) {
        if (active) {
          setError(
            err instanceof Error ? err.message : "Failed to connect to stream"
          );
          // Retry connection after 3 seconds if stream isn't up
          setTimeout(startWebRTC, 3000);
        }
      }
    };

    startWebRTC();

    return () => {
      active = false;
      if (pcRef.current) {
        pcRef.current.close();
      }
    };
  }, [cameraId]);

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
