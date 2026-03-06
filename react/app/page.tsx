"use client";

import React, { useState, useEffect, useRef } from 'react';
import 'bootstrap/dist/css/bootstrap.min.css';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { 
  faCircleNotch, 
  faExclamationTriangle,
  faDownload,
  faCopy,
  faCheck,
  faPlay,
  faStop
} from '@fortawesome/free-solid-svg-icons';

export default function VideoConverter() {
  const [url, setUrl] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);
  const [status, setStatus] = useState<string | null>("loading");
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [fileSize, setFileSize] = useState<number>(0);
  const [videoTitle, setVideoTitle] = useState<string | null>(null);
  const [error, setError] = useState<null | string>(null);
  const [copied, setCopied] = useState(false);
  const [streamUrl, setStreamUrl] = useState('');
  const [thumbnailUrl, setThumbnailUrl] = useState('');
  const [progression, setProgression] = useState(0);

  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL?.replace(/\/+$/, "") || "";
  const pollInterval = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    return () => { if (pollInterval.current) clearInterval(pollInterval.current); };
  }, []);

  const startPolling = (targetUrl: string) => {
    if (pollInterval.current) clearTimeout(pollInterval.current as NodeJS.Timeout);
    
    const poll = async () => {
      try {
        const res = await fetch(`${siteUrl}/api/status?url=${encodeURIComponent(targetUrl)}`);
        
        if (!res.ok) {
          setError(`Server error: ${res.status}`);
          setIsProcessing(false);
          setStatus("loading");
          setThumbnailUrl("");
          setProgression(0);
          setFileSize(0);
          return; 
        }

        const data = await res.json();

        setStatus(data.status);
        if(data.message) {
          setStatusMsg(data.message)
        }
        else {
          setStatusMsg(null)
        }
        setProgression(data.progression || 0);
        setFileSize(data.current_size || 0);
        
        if (data.status === 'completed') {
          setIsProcessing(false);
          if (pollInterval.current) clearTimeout(pollInterval.current as NodeJS.Timeout);
          return;
        } else if (data.status === 'error' || data.status === 'failed') {
          setError("Processing failed on server.");
          setIsProcessing(false);
          setStatus("loading");
          setThumbnailUrl("");
          setProgression(0);
          setFileSize(0);
          if (pollInterval.current) clearTimeout(pollInterval.current as NodeJS.Timeout);
          return;
        }

        pollInterval.current = setTimeout(poll, 2000);

      } catch (err) {
        console.warn("Polling error (possible background/network issue):", err);
        setStatus("reconnecting"); 
        pollInterval.current = setTimeout(poll, 5000); 
      }
    };

    poll();
  };

  const handleSubmit = async (e: React.SyntheticEvent<HTMLFormElement, SubmitEvent>) => {
    e.preventDefault();
    setStatus("loading");
    setThumbnailUrl("");
    setProgression(0);
    setFileSize(0);
    setError(null);
    if (!url) return;
    setIsProcessing(true);
    setVideoTitle(null);

    try {
      const response = await fetch(`${siteUrl}/api/create?url=${encodeURIComponent(url)}`, {
        method: 'POST',
      });
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.message || `Error: ${response.status}`);
      }

      setVideoTitle(data.title);
      setStreamUrl(data.stream);
      setThumbnailUrl(data.thumbnail);
      startPolling(url);
      
    } catch (err: unknown) {
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('An unexpected error occurred');
      }

      setIsProcessing(false);
      setStatus("loading");
      setThumbnailUrl("");
      setProgression(0);
      setFileSize(0);
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(streamUrl).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1000);
    });
  };

  const formatSize = (b: number) => {
    if (b === 0) return '0 B';
    if (b >= 1024 * 1024 * 1024) return `${(b / (1024 * 1024 * 1024)).toFixed(2)} GB`;
    if (b >= 1024 * 1024) return `${(b / (1024 * 1024)).toFixed(2)} MB`;
    if (b >= 1024) return `${(b / 1024).toFixed(2)} KB`;
    return `${b.toFixed(2)} B`;
  };
  
  return (
    <div className="container-fluid min-vh-100 bg-light py-5">
      <div className="row justify-content-center">
        <div className="col-12 col-md-8 col-lg-6">
          
          <div className="text-center mb-5">
            <h1 className="display-5 fw-bold text-dark">RBTV-DL</h1>
          </div>

          {/* Search Card */}
          <div className="card shadow border-0 p-3 p-md-4 mb-4">
            <form onSubmit={handleSubmit}>
              <div className="input-group">
                <input
                  type="url"
                  className="form-control form-control-lg border-primary-subtle"
                  placeholder="Paste video link here..."
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  disabled={isProcessing}
                  required
                  autoFocus
                  onClick={(e) => (e.target as HTMLInputElement).select()}
                  style={{ height: '48px' }}
                />
                <button
                  className="btn btn-dark d-flex align-items-center justify-content-center"
                  type="submit"
                  disabled={isProcessing}
                  style={{ width: '48px', height: '48px', flexShrink: 0 }}
                >
                  {isProcessing ? (
                    <FontAwesomeIcon icon={faCircleNotch} spin style={{ width: '1rem' }} />
                  ) : (
                    <FontAwesomeIcon icon={faPlay} style={{ width: '1rem' }} />
                  )}
                </button>
              </div>
            </form>
          </div>

          {/* Error Message */}
          {error && (
            <div className="alert alert-danger alert-dismissible fade show d-flex align-items-center" role="alert">
              <FontAwesomeIcon icon={faExclamationTriangle} className="me-2" />
              <div><strong>Error:</strong> {error}</div>
              <button type="button" className="btn-close" onClick={() => setError(null)}></button>
            </div>
          )}

          {/* Status/Result Card */}
          {(isProcessing || status === 'completed') && (
            <div className="card shadow-sm border-0 animate-fade-in">
              <div className="card-body d-flex align-items-center p-3">
                {/* Thumbnail / Status Square */}
                <div 
                  className="rounded me-3 bg-dark d-flex align-items-center justify-content-center text-white" 
                  style={{ 
                    width: '80px', 
                    height: '80px', 
                    flexShrink: 0, 
                    position: 'relative', 
                    overflow: 'hidden' 
                  }}
                >
                  {thumbnailUrl && (
                    <img 
                      src={thumbnailUrl} 
                      alt="Thumbnail"
                      referrerPolicy="no-referrer"
                      style={{
                        position: 'absolute',
                        top: 0,
                        left: 0,
                        width: '100%',
                        height: '100%',
                        objectFit: 'cover'
                      }}
                    />
                  )}

                  {/* Status Overlay */}
                  <div 
                    style={{
                      position: 'absolute',
                      top: 0, 
                      left: 0, 
                      right: 0, 
                      bottom: 0,
                      backgroundColor: status !== 'completed' ? 'rgba(0,0,0,0.4)' : 'rgba(0,0,0,0.2)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      transition: 'background-color 0.3s ease',
                      zIndex: 1
                    }}
                  >
                    {status !== 'completed' && (
                      <FontAwesomeIcon 
                        icon={faCircleNotch} 
                        spin 
                        size="2x" 
                        className="text-white" 
                        style={{ filter: 'drop-shadow(0px 0px 4px rgba(0,0,0,0.8))' }} 
                      />
                    )}
                    {status === 'completed' && (
                      <FontAwesomeIcon 
                        icon={faCheck} 
                        size="2x" 
                        className="text-white" 
                        style={{ 
                          filter: 'drop-shadow(0px 0px 6px rgba(0,0,0,0.9))',
                          opacity: 0.9 
                        }} 
                      />
                    )}
                  </div>
                </div>

                <div className="flex-grow-1 overflow-hidden">
                  <h6 className="text-truncate mb-1">{videoTitle || "Loading..."}</h6>
                  
                  {/* Status Indicator */}
                  <div className="small text-muted mb-2" style={{ minHeight: '24px' }}>
                    {status === 'loading' && (
                      <span className="d-flex align-items-center">
                        <FontAwesomeIcon icon={faCircleNotch} spin className="me-2 text-primary" />
                        Initializing...
                      </span>
                    )}
                    {status === 'converting' && (
                      <span className="d-flex align-items-center">
                        <FontAwesomeIcon icon={faCircleNotch} spin className="me-2 text-primary" />
                        Converting: {progression} %
                      </span>
                    )}
                    {status === 'reconnecting' && (
                      <span className="d-flex align-items-center text-secondary">
                        <FontAwesomeIcon icon={faCircleNotch} spin className="me-2 text-muted" />
                        Reconnecting...
                      </span>
                    )}
                    {status === 'finalizing' && (
                      <span className="d-flex align-items-center text-warning">
                        <FontAwesomeIcon icon={faCircleNotch} spin className="me-2" />
                        Finalizing {statusMsg && `(${statusMsg})`}
                      </span>
                    )}
                    {status === 'completed' && (
                      <span className="text-success">
                        <FontAwesomeIcon icon={faCheck} className="me-2" />
                        Ready ({formatSize(fileSize)})
                      </span>
                    )}
                  </div>

                  {/* Buttons */}
                  <div className="d-flex gap-2">
                    <button 
                      className={`btn btn-sm ${copied ? 'btn-success' : 'btn-outline-success'}`} 
                      onClick={handleCopy}
                    >
                      <FontAwesomeIcon icon={copied ? faCheck : faCopy} className="me-1" />
                      Copy M3U Link
                    </button>
                    
                    <a 
                      href={`${siteUrl}/api/download?url=${encodeURIComponent(url)}`}
                      className={`btn btn-sm ${status === 'completed' ? 'btn-outline-dark' : 'btn-outline-secondary disabled'}`}
                    >
                      <FontAwesomeIcon icon={faDownload} className="me-2" />
                      Download MP4
                    </a>
                  </div>
                </div>
              </div>
            </div>
          )}

        </div>
      </div>
    </div>
  );
}