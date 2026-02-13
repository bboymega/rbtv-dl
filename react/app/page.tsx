"use client";

import React, { useState } from 'react';
import 'bootstrap/dist/css/bootstrap.min.css';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { 
  faCircleNotch, 
  faExclamationTriangle,
  faDownload,
  faCopy,
  faCheck,
  faPlay
} from '@fortawesome/free-solid-svg-icons';
import config from './config.json';

export default function VideoConverter() {
  const [url, setUrl] = useState('');
  const [isParsing, setIsParsing] = useState(false);
  const [isWaitingDownload, setIsWaitingDownload] = useState(false);
  const [videoData, setVideoData] = useState<null | { title: string; video_url: string; video_thumbnail: string }>(null);
  const [error, setError] = useState<null | string>(null);
  const [copied, setCopied] = useState(false);
  const apiBase = config.apiBase.replace(/\/+$/, "");
  
  const handleParse = async (e: React.SubmitEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError(null);
    if (!url) return;
    setIsParsing(true);
    setVideoData(null);
    try {
      const response = await fetch(apiBase + '/api/download?url=' + url + '&probe=1', {
        method: 'GET',
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        setError(errorData.message)
        throw new Error(errorData.message || `Error: ${response.status}`);
      }

      const data = await response.json();
      setVideoData(data);
      
    } catch (err) {
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('An unexpected error occurred');
      }
    } finally {
      setIsParsing(false);
    }
  };

  const handleCopy = () => {
    if (videoData?.video_url) {
      navigator.clipboard.writeText(videoData.video_url).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      });
    }
  };

  const handleWindowFocus = () => {
    setIsWaitingDownload(false);
    window.removeEventListener('focus', handleWindowFocus);
  };

  const handleDownload = () => {
    window.addEventListener('focus', handleWindowFocus);
    setIsWaitingDownload(true);
  }

  return (
    <div className="container-fluid min-vh-100 bg-light py-5">
      <div className="row justify-content-center">
        <div className="col-12 col-md-8 col-lg-6">
          
          <div className="text-center mb-5">
            <h1 className="display-5 fw-bold text-dark">RBTV Converter</h1>
          </div>

          <div className="card shadow border-0 p-3 p-md-4 mb-4">
            <form onSubmit={handleParse}>
              <div className="input-group">
                <input
                  type="url"
                  className="form-control form-control-lg border-primary-subtle"
                  placeholder="Paste video link here..."
                  value={url}
                  id="videoUrl"
                  autoFocus
                  onChange={(e) => setUrl(e.target.value)}
                  onFocus={(e) => e.target.select()}
                  pattern="^https?://www\.(redbull\.com|redbull\.tv).*"
                  required
                  style={{ height: '48px' }} // Matches the standard height of form-control-lg
                />
                <button
                  className="btn btn-dark d-flex align-items-center justify-content-center"
                  type="submit"
                  disabled={isParsing}
                  style={{ 
                    width: '48px', 
                    height: '48px', 
                    padding: '0', 
                    flexShrink: 0 
                  }}
                >
                  {isParsing ? (
                    <FontAwesomeIcon 
                      icon={faCircleNotch} 
                      spin 
                      style={{ width: '1rem', height: '1rem' }} 
                    />
                  ) : (
                    <FontAwesomeIcon 
                      icon={faPlay} 
                      style={{ width: '1rem', height: '1rem' }} 
                    />
                  )}
                </button>
              </div>
            </form>
          </div>
          {error && (
            <div 
              className="alert alert-danger alert-dismissible fade show d-flex align-items-center" 
              role="alert"
            >
              <div>
                <FontAwesomeIcon 
                  icon={faExclamationTriangle} 
                  className="me-2" 
                />
                <strong>Error:</strong> {error}
              </div>
              <button 
                type="button" 
                className="btn-close" 
                onClick={() => setError(null)} 
                aria-label="Close"
              ></button>
            </div>
          )}
          {videoData && (
            <div className="card shadow-sm border-0 animate-fade-in">
              <div className="card-body d-flex align-items-center p-3">
                <div 
                  className="rounded me-3 bg-light d-flex align-items-center justify-content-center" 
                  style={{ width: '80px', height: '80px', flexShrink: 0, overflow: 'hidden' }}
                >
                  <img 
                    src={videoData.video_thumbnail} 
                    alt="Thumbnail" 
                    referrerPolicy="no-referrer"
                    style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                  />
                </div>
                <div className="flex-grow-1 overflow-hidden">
                  <h6 className="text-truncate mb-1">{videoData.title}</h6>
                  <div className="d-flex gap-2 mt-2">
                    <button 
                      className={`btn btn-sm ${copied ? 'btn-success' : 'btn-outline-success'}`} 
                      onClick={handleCopy}
                    >
                      {copied ? (
                        <>
                          <FontAwesomeIcon 
                            icon={faCheck} 
                            className="me-1"
                          />
                            Copy M3U Link
                        </>
                      ) : (
                        <>
                          <FontAwesomeIcon 
                            icon={faCopy} 
                            className="me-1" 
                          />
                          Copy M3U Link
                        </>
                      )}
                    </button>
                    <a 
                      href= {`${apiBase}/api/download?url=${encodeURIComponent(url)}`}
                      className={`btn btn-sm ${isWaitingDownload ? 'btn-secondary' : 'btn-outline-secondary'}`}
                      onClick={() => handleDownload()}
                    >
                      <>
                        {isWaitingDownload ? (
                            <FontAwesomeIcon 
                              icon={faCircleNotch} 
                              spin 
                              className="me-2" 
                            />
                        ) : (
                          <FontAwesomeIcon 
                            icon={faDownload} 
                            className="me-2" 
                          />
                        )}
                        Convert & Download
                      </>
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