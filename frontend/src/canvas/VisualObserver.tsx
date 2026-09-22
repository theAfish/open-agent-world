import { useEffect } from 'react';
import { runtimeWebSocketUrl } from '../api/client';
import { captureObservation, type ObservationRequest } from './visualObservation';
import { capturePluginVisual } from '../plugins/visualCapture';

type PluginCaptureRequest = {
  kind: 'plugin_capture'; request_id: string; node_id: string; capture_kind: string;
  document_revision: number; max_image_dimension: number; capture_options?: Record<string, unknown>;
};

function isPluginCaptureRequest(request: ObservationRequest | PluginCaptureRequest): request is PluginCaptureRequest {
  return 'kind' in request && request.kind === 'plugin_capture';
}

export function VisualObserver() {
  useEffect(() => {
    let active = true;
    let socket: WebSocket | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let work = Promise.resolve();
    const connect = () => {
      const url = new URL(runtimeWebSocketUrl());
      url.pathname = url.pathname.replace(/\/events$/, '/visual');
      const current = socket = new WebSocket(url);
      current.onmessage = event => {
        const request = JSON.parse(event.data) as ObservationRequest | PluginCaptureRequest;
        work = work.then(async () => {
          if (!active || current.readyState !== WebSocket.OPEN) return;
          let response;
          try {
            if (isPluginCaptureRequest(request)) {
              const capture = await capturePluginVisual({
                nodeId: request.node_id, captureKind: request.capture_kind,
                documentRevision: request.document_revision,
                maxImageDimension: request.max_image_dimension,
                captureOptions: request.capture_options,
              });
              response = {
                request_id: request.request_id, kind: request.kind, node_id: request.node_id,
                capture_kind: request.capture_kind, document_revision: request.document_revision,
                data_base64: capture.dataBase64,
                metadata: capture.metadata,
              };
            } else response = await captureObservation(request);
          }
          catch { response = { request_id: request.request_id, error: 'Unable to capture this canvas. Keep OAW open and retry.' }; }
          if (active && current.readyState === WebSocket.OPEN) current.send(JSON.stringify(response));
        });
      };
      current.onclose = () => { if (active) timer = setTimeout(connect, 3000); };
      current.onerror = () => current.close();
    };
    connect();
    return () => { active = false; clearTimeout(timer); socket?.close(); };
  }, []);
  return null;
}
