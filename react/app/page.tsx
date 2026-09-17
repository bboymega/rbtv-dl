"use client";

import React, {
  useState,
  useEffect,
  useRef,
  useCallback,
} from 'react';
import 'bootstrap/dist/css/bootstrap.min.css';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faCircleNotch,
  faExclamationTriangle,
  faDownload,
  faCopy,
  faCheck,
  faPlay,
  faXmark,
  faRotateRight,
} from '@fortawesome/free-solid-svg-icons';

type DownloadStatus =
  | "loading"
  | "converting"
  | "finalizing"
  | "reconnecting"
  | "completed"
  | "error"
  | "failed";

interface DownloadItem {
  id: string;
  url: string;
  title: string;
  thumbnail: string;
  subheading: string;
  status: DownloadStatus;
  progress: number;
  fileSize: number;
  streamUrl: string;
  createdAt: number;
}

interface DownloadCheckResult {
  ready: boolean;
  gone: boolean;
}

const STORAGE_KEY = "rbtvdl_recent_downloads";
const MAX_RECENT_DOWNLOADS = 20;

export default function VideoConverter() {
  const [url, setUrl] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);
  const [status, setStatus] = useState<DownloadStatus | null>("loading");
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [fileSize, setFileSize] = useState<number>(0);
  const [videoTitle, setVideoTitle] = useState<string | null>(null);
  const [error, setError] = useState<null | string>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [streamUrl, setStreamUrl] = useState('');
  const [thumbnailUrl, setThumbnailUrl] = useState('');
  const [progression, setProgression] = useState(0);
  const [subheading, setSubheading] = useState<null | string>(null);

  const [recentDownloads, setRecentDownloads] = useState<DownloadItem[]>([]);
  const [showRecentDownloads, setShowRecentDownloads] = useState(false);

  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL?.replace(/\/+$/, "") || "";

  const pollTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const currentTaskIdRef = useRef("");

  /*
   * ---------------------------------------------------------
   * Helpers
   * ---------------------------------------------------------
   */

  const formatSize = (b: number) => {
    if (b === 0) return '0 B';
    if (b >= 1024 * 1024 * 1024) return `${(b / (1024 * 1024 * 1024)).toFixed(2)} GB`;
    if (b >= 1024 * 1024) return `${(b / (1024 * 1024)).toFixed(2)} MB`;
    if (b >= 1024) return `${(b / 1024).toFixed(2)} KB`;
    return `${b.toFixed(2)} B`;
  };

  const isTerminalStatus = (value: DownloadStatus) =>
    value === "completed" ||
    value === "failed" ||
    value === "error";

  const isActiveStatus = (value: DownloadStatus) =>
    !isTerminalStatus(value);

    const getProgressBarClass = (value: DownloadStatus) => {
    switch (value) {
        case "finalizing":
        return "bg-warning";

        case "reconnecting":
        return "bg-secondary";

        default:
        return "bg-primary";
    }
    };

  /*
   * ---------------------------------------------------------
   * History storage
   * ---------------------------------------------------------
   */

  const saveRecentDownloads = (items: DownloadItem[]) => {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify(items.slice(0, MAX_RECENT_DOWNLOADS))
      );
    } catch (err) {
      console.warn("Unable to save recent downloads:", err);
    }
  };

  /*
   * Same deduplication logic as YTDAlpha:
   *
   * Add the new item to the front, then remove any existing
   * item with the same ID.
   *
   * For RBTV-DL the URL is the ID.
   */
  const addRecentDownload = (item: DownloadItem) => {
    setRecentDownloads((current) => {
      const updated = [
        item,
        ...current.filter(
          (existing) => existing.id !== item.id
        ),
      ].slice(0, MAX_RECENT_DOWNLOADS);

      saveRecentDownloads(updated);

      return updated;
    });
  };

  const updateRecentDownload = (
    id: string,
    updates: Partial<DownloadItem>
  ) => {
    setRecentDownloads((current) => {
      const updated = current.map((item) =>
        item.id === id
          ? { ...item, ...updates }
          : item
      );

      saveRecentDownloads(updated);

      return updated;
    });
  };

  const clearPolling = (id: string) => {
    const timer = pollTimers.current[id];

    if (timer) {
      clearTimeout(timer);
      delete pollTimers.current[id];
    }
  };

  /*
   * ---------------------------------------------------------
   * Download availability check
   *
   * EXACT SAME LOGIC AS YTDAlpha:
   *
   * 200/OK  -> ready
   * 410     -> gone
   * other   -> not ready, not gone
   * network -> not ready, not gone
   *
   * RBTV uses the URL instead of YTDAlpha's id/audio_only.
   * ---------------------------------------------------------
   */

  const checkDownloadAvailability = useCallback(
    async (
      targetUrl: string
    ): Promise<DownloadCheckResult> => {
      try {
        const response = await fetch(
          `${siteUrl}/api/download?url=${encodeURIComponent(
            targetUrl
          )}`,
          {
            method: "HEAD",
            cache: "no-store",
          }
        );

        if (response.ok) {
          return {
            ready: true,
            gone: false,
          };
        }

        if (response.status === 404) {
          return {
            ready: false,
            gone: true,
          };
        }

        return {
          ready: false,
          gone: false,
        };
      } catch (err) {
        console.warn("Download HEAD check failed:", err);

        return {
          ready: false,
          gone: false,
        };
      }
    },
    [siteUrl]
  );

  const markTaskAsFailed = (targetUrl: string) => {
    updateRecentDownload(targetUrl, {
      status: "failed",
      progress: 0,
      fileSize: 0,
    });

    if (currentTaskIdRef.current === targetUrl) {
      setStatus("failed");
      setProgression(0);
      setFileSize(0);
      setIsProcessing(false);
    }

    clearPolling(targetUrl);
  };

  /*
   * ---------------------------------------------------------
   * Polling
   * ---------------------------------------------------------
   */

  const startPolling = useCallback(
    (targetUrl: string) => {
      clearPolling(targetUrl);

      const poll = async () => {
        try {
          const res = await fetch(
            `${siteUrl}/api/status?url=${encodeURIComponent(targetUrl)}`,
            {
              cache: "no-store",
            }
          );

          if (!res.ok) {
            throw new Error(`Server error: ${res.status}`);
          }

          const data = await res.json();

          const newStatus = data.status as DownloadStatus;
          const newProgression = Number(data.progression || 0);
          const newFileSize = Number(data.current_size || 0);
          const newStatusMsg = data.message || null;

          /*
           * -------------------------------------------------
           * Failed
           * -------------------------------------------------
           */

          if (
            newStatus === 'error' ||
            newStatus === 'failed'
          ) {
            updateRecentDownload(targetUrl, {
              status: newStatus,
              progress: newProgression,
              fileSize: newFileSize,
            });

            if (currentTaskIdRef.current === targetUrl) {
              setStatus(newStatus);
              setStatusMsg(newStatusMsg);
              setProgression(newProgression);
              setFileSize(newFileSize);
              setIsProcessing(false);
            }

            clearPolling(targetUrl);
            return;
          }

          /*
           * -------------------------------------------------
           * Completed
           *
           * EXACT SAME AVAILABILITY CHECK LOGIC AS YTDAlpha.
           *
           * This is also what makes completed downloads get
           * re-checked after a page reload, because restored
           * completed items are passed through startPolling().
           * -------------------------------------------------
           */

          if (newStatus === 'completed') {
            const downloadCheck =
              await checkDownloadAvailability(targetUrl);

            if (downloadCheck.ready) {
              updateRecentDownload(targetUrl, {
                status: "completed",
                progress: 100,
                fileSize: newFileSize,
              });

              if (currentTaskIdRef.current === targetUrl) {
                setStatus("completed");
                setStatusMsg(newStatusMsg);
                setProgression(100);
                setFileSize(newFileSize);
                setIsProcessing(false);
              }

              clearPolling(targetUrl);
              return;
            }

            if (downloadCheck.gone) {
              markTaskAsFailed(targetUrl);
              return;
            }

            /*
             * Same behavior as YTDAlpha's "merging" state,
             * using RBTV's existing "finalizing" state.
             */
            updateRecentDownload(targetUrl, {
              status: "finalizing",
              progress: 100,
              fileSize: newFileSize,
            });

            if (currentTaskIdRef.current === targetUrl) {
              setStatus("finalizing");
              setStatusMsg(newStatusMsg);
              setProgression(100);
              setFileSize(newFileSize);
              setIsProcessing(true);
            }

            pollTimers.current[targetUrl] = setTimeout(
              poll,
              2000
            );

            return;
          }

          /*
           * -------------------------------------------------
           * Normal status update
           * -------------------------------------------------
           */

          updateRecentDownload(targetUrl, {
            status: newStatus,
            progress: newProgression,
            fileSize: newFileSize,
          });

          if (currentTaskIdRef.current === targetUrl) {
            setStatus(newStatus);
            setStatusMsg(newStatusMsg);
            setProgression(newProgression);
            setFileSize(newFileSize);
          }

          pollTimers.current[targetUrl] = setTimeout(
            poll,
            2000
          );
        } catch (err) {
          console.warn(
            "Polling error (possible background/network issue):",
            err
          );

          updateRecentDownload(targetUrl, {
            status: "reconnecting",
          });

          if (currentTaskIdRef.current === targetUrl) {
            setStatus("reconnecting");
            setIsProcessing(true);
          }

          pollTimers.current[targetUrl] = setTimeout(
            poll,
            5000
          );
        }
      };

      poll();
    },
    [checkDownloadAvailability, siteUrl]
  );

  /*
   * ---------------------------------------------------------
   * Restore history
   * ---------------------------------------------------------
   */

  useEffect(() => {
    let storedItems: DownloadItem[] = [];

    try {
      const stored = localStorage.getItem(STORAGE_KEY);

      if (stored) {
        const parsed = JSON.parse(stored);

        if (Array.isArray(parsed)) {
          storedItems = parsed;
        }
      }
    } catch (err) {
      console.warn(
        "Unable to restore recent downloads:",
        err
      );
    }

    if (storedItems.length > 0) {
      setRecentDownloads(storedItems);

      /*
       * Resume unfinished downloads AND re-check completed
       * downloads.
       *
       * startPolling() handles the completed-task HEAD check
       * exactly like YTDAlpha.
       */
      storedItems.forEach((item) => {
        startPolling(item.url);
      });
    }

    return () => {
      Object.values(pollTimers.current).forEach(
        (timer) => clearTimeout(timer)
      );

      pollTimers.current = {};
    };
  }, [startPolling]);

  /*
   * ---------------------------------------------------------
   * Create
   * ---------------------------------------------------------
   */

  const handleSubmit = async (
    e: React.SyntheticEvent<HTMLFormElement, SubmitEvent>,
    overrideUrl?: string
    ) => {
    e.preventDefault();

    const submittedUrl = (
        overrideUrl !== undefined
        ? overrideUrl
        : url
    ).trim();

    setStatus("loading");
    setStatusMsg(null);
    setThumbnailUrl("");
    setProgression(0);
    setFileSize(0);
    setError(null);

    if (!submittedUrl) return;

    // Keep the input box in sync when Convert Again is used.
    setUrl(submittedUrl);

    setIsProcessing(true);
    setVideoTitle(null);
    setSubheading(null);
    setStreamUrl("");

    const taskId = submittedUrl;

    currentTaskIdRef.current = taskId;

    try {
        const response = await fetch(`${siteUrl}/api/create`, {
        method: 'POST',
        body: JSON.stringify({
            url: submittedUrl,
        }),
        headers: {
            'Content-Type': 'application/json',
        },
        });

        const data = await response.json();

        if (!response.ok) {
        throw new Error(
            data.message || `Error: ${response.status}`
        );
        }

        const title = data.title || "Untitled";
        const thumbnail = data.thumbnail || "";
        const newSubheading = data.subheading || "";
        const newStreamUrl = data.stream || "";

        setVideoTitle(title);
        setSubheading(newSubheading);
        setStreamUrl(newStreamUrl);
        setThumbnailUrl(thumbnail);

        addRecentDownload({
        id: taskId,
        url: submittedUrl,
        title,
        thumbnail,
        subheading: newSubheading,
        status: "loading",
        progress: 0,
        fileSize: 0,
        streamUrl: newStreamUrl,
        createdAt: Date.now(),
        });

        startPolling(submittedUrl);
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

    const handleConvertAgain = (
    item: DownloadItem
    ) => {
    setError(null);

    // Put the URL into the input AND immediately submit it.
    handleSubmit(
        {
        preventDefault: () => {},
        } as React.SyntheticEvent<HTMLFormElement, SubmitEvent>,
        item.url
    );
    };

  /*
   * ---------------------------------------------------------
   * Copy M3U
   * ---------------------------------------------------------
   */

  const handleCopy = (
    targetStreamUrl?: string,
    targetId?: string
    ) => {
    const value = targetStreamUrl || streamUrl;

    if (!value || !targetId) return;

    navigator.clipboard.writeText(value).then(() => {
        setCopiedId(targetId);

        setTimeout(() => {
        setCopiedId((current) =>
            current === targetId ? null : current
        );
        }, 1000);
    });
    };

  /*
   * ---------------------------------------------------------
   * History actions
   * ---------------------------------------------------------
   */

  const handleDownload = async (
    item: DownloadItem
  ) => {
    setError(null);

    /*
     * EXACT SAME LOGIC AS YTDAlpha:
     *
     * Check availability before starting the download.
     */
    const downloadCheck =
      await checkDownloadAvailability(item.url);

    if (downloadCheck.gone) {
      markTaskAsFailed(item.url);
      return;
    }

    if (!downloadCheck.ready) {
      markTaskAsFailed(item.url);

      setError(
        "The download is no longer available. Please convert it again."
      );

      return;
    }

    window.location.href =
      `${siteUrl}/api/download?url=${encodeURIComponent(
        item.url
      )}`;
  };

  /*
   * Dismiss ONLY the history item.
   *
   * This intentionally does not:
   * - stop polling
   * - clear the current task
   * - change current conversion state
   */
  const dismissHistoryItem = (
    id: string
  ) => {
    setRecentDownloads((current) => {
      const updated = current.filter(
        (item) => item.id !== id
      );

      saveRecentDownloads(updated);

      return updated;
    });
  };

  const clearHistory = () => {
    setRecentDownloads((current) => {
      saveRecentDownloads([]);

      return current.length === 0
        ? current
        : [];
    });
  };

  /*
   * ---------------------------------------------------------
   * Shared history card
   * ---------------------------------------------------------
   */

  const DownloadItemCard = ({
    item,
    showDismiss = false,
  }: {
    item: DownloadItem;
    showDismiss?: boolean;
  }) => {
    const progress = Math.min(
      100,
      Math.max(0, item.progress)
    );
    
    return (
      <div className="card shadow-sm border-0">
        <div className="card-body p-3">
          <div className="d-flex align-items-center">

            {/* Thumbnail */}
            <div
              className="rounded me-3 bg-dark d-flex align-items-center justify-content-center text-white"
              style={{
                width: '80px',
                height: '80px',
                flexShrink: 0,
                position: 'relative',
                overflow: 'hidden',
              }}
            >
              {item.thumbnail && (
                <img
                  src={item.thumbnail}
                  alt="Thumbnail"
                  referrerPolicy="no-referrer"
                  style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    width: '100%',
                    height: '100%',
                    objectFit: 'cover',
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
                  backgroundColor:
                    item.status !== 'completed'
                      ? 'rgba(0,0,0,0.4)'
                      : 'rgba(0,0,0,0.2)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  zIndex: 1,
                }}
              >
                {isActiveStatus(item.status) && (
                  <FontAwesomeIcon
                    icon={faCircleNotch}
                    spin
                    size="2x"
                    className="text-white"
                    style={{
                      filter:
                        'drop-shadow(0px 0px 4px rgba(0,0,0,0.8))',
                    }}
                  />
                )}

                {item.status === 'completed' && (
                  <FontAwesomeIcon
                    icon={faCheck}
                    size="2x"
                    className="text-white"
                    style={{
                      filter:
                        'drop-shadow(0px 0px 6px rgba(0,0,0,0.9))',
                      opacity: 0.9,
                    }}
                  />
                )}

                {(item.status === 'error' ||
                  item.status === 'failed') && (
                  <FontAwesomeIcon
                    icon={faExclamationTriangle}
                    size="lg"
                    className="text-white"
                  />
                )}
              </div>
            </div>

            {/* Information */}
            <div className="flex-grow-1 overflow-hidden">
              <h6
                className="text-truncate mb-1"
                title={item.title}
              >
                {item.title}
              </h6>

              <div
                className="small text-muted mb-2 text-truncate"
                style={{
                  minHeight: '24px',
                }}
              >
                {item.subheading ||
                  item.url}
              </div>

              <div className="small">
                {item.status === 'loading' && (
                  <span className="d-flex align-items-center text-dark">
                    <FontAwesomeIcon
                      icon={faCircleNotch}
                      spin
                      className="me-2 text-primary"
                    />
                    Loading...
                  </span>
                )}

                {item.status === 'converting' && (
                  <span className="d-flex align-items-center text-dark">
                    <FontAwesomeIcon
                      icon={faCircleNotch}
                      spin
                      className="me-2 text-primary"
                    />
                    Converting: {item.progress} %
                  </span>
                )}

                {item.status === 'reconnecting' && (
                  <span className="d-flex align-items-center text-secondary">
                    <FontAwesomeIcon
                      icon={faCircleNotch}
                      spin
                      className="me-2"
                    />
                    Reconnecting...
                  </span>
                )}

                {item.status === 'finalizing' && (
                  <span className="d-flex align-items-center text-warning">
                    <FontAwesomeIcon
                      icon={faCircleNotch}
                      spin
                      className="me-2"
                    />
                    Finalizing
                  </span>
                )}

                {item.status === 'completed' && (
                  <span className="text-success">
                    <FontAwesomeIcon
                      icon={faCheck}
                      className="me-2"
                    />
                    Ready ({formatSize(item.fileSize)})
                  </span>
                )}

                {(item.status === 'error' ||
                  item.status === 'failed') && (
                  <span className="text-danger">
                    <FontAwesomeIcon
                      icon={faExclamationTriangle}
                      className="me-2"
                    />
                    Not Available
                  </span>
                )}
              </div>
            </div>

            {/* Buttons */}
            <div
              className="d-flex align-items-center gap-2 ms-2"
              style={{
                flexShrink: 0,
              }}
            >
              {item.status === 'completed' && (
                <>
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-success"
                    onClick={() =>
                      handleCopy(item.streamUrl, item.id)
                    }
                  >
                    <FontAwesomeIcon
                      icon={
                        copiedId === item.id
                          ? faCheck
                          : faCopy
                      }
                      className="me-1"
                    />

                    <span className="d-none d-lg-inline">
                      Copy M3U
                    </span>
                  </button>

                  <button
                    type="button"
                    className="btn btn-sm btn-outline-dark"
                    onClick={() =>
                      handleDownload(item)
                    }
                  >
                    <FontAwesomeIcon
                      icon={faDownload}
                      className="me-1"
                    />

                    <span className="d-none d-sm-inline">
                      Download
                    </span>
                  </button>
                </>
              )}

              {(item.status === 'error' ||
                item.status === 'failed') && (
                <button
                  type="button"
                  className="btn btn-sm btn-outline-dark"
                  onClick={() =>
                    handleConvertAgain(item)
                  }
                >
                  <FontAwesomeIcon
                    icon={faRotateRight}
                    className="me-1"
                  />

                  <span className="d-none d-sm-inline">
                    Convert Again
                  </span>
                </button>
              )}

              {showDismiss && (
                <button
                  type="button"
                  className="btn btn-sm btn-link text-muted p-1"
                  aria-label={`Dismiss ${item.title}`}
                  title="Dismiss"
                  onClick={() =>
                    dismissHistoryItem(item.id)
                  }
                  style={{
                    lineHeight: 1,
                    textDecoration: 'none',
                  }}
                >
                  <FontAwesomeIcon icon={faXmark} />
                </button>
              )}
            </div>
          </div>

          {/* Progress */}
          {isActiveStatus(item.status) && (
            <div className="mt-2">
              <div
                className="progress"
                style={{
                  height: '4px',
                }}
              >
                <div
                    className={`progress-bar ${getProgressBarClass(
                        item.status
                    )}`}
                    role="progressbar"
                    style={{
                        width: `${progress}%`,
                    }}
                    aria-valuenow={progress}
                    aria-valuemin={0}
                    aria-valuemax={100}
                />
              </div>
            </div>
          )}
        </div>
      </div>
    );
  };

  /*
   * ---------------------------------------------------------
   * Current conversion
   *
   * IMPORTANT:
   * History dismissal does not remove this.
   * ---------------------------------------------------------
   */

  const activeHistoryItem =
    recentDownloads.find(
      (item) =>
        item.id === currentTaskIdRef.current
    );

  const currentConversionItem: DownloadItem | null =
    activeHistoryItem ||
    (currentTaskIdRef.current
      ? {
          id: currentTaskIdRef.current,
          url,
          title:
            videoTitle || "Loading...",
          thumbnail: thumbnailUrl,
          subheading:
            subheading || "",
          status:
            status || "loading",
          progress: progression,
          fileSize,
          streamUrl,
          createdAt: Date.now(),
        }
      : null);

  return (
    <div className="container-fluid min-vh-100 bg-light py-5">
      <div className="row justify-content-center">
        <div className="col-12 col-md-8 col-lg-6">

          <div className="text-center mb-5">
            <h1 className="display-5 fw-bold text-dark">
              RBTV-DL
            </h1>
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
                  onChange={(e) =>
                    setUrl(e.target.value)
                  }
                  disabled={isProcessing}
                  required
                  autoFocus
                  onClick={(e) =>
                    (
                      e.target as HTMLInputElement
                    ).select()
                  }
                  style={{
                    height: '48px',
                  }}
                />

                <button
                  className="btn btn-dark d-flex align-items-center justify-content-center"
                  type="submit"
                  disabled={isProcessing}
                  style={{
                    width: '48px',
                    height: '48px',
                    flexShrink: 0,
                  }}
                >
                  {isProcessing ? (
                    <FontAwesomeIcon
                      icon={faCircleNotch}
                      spin
                      style={{
                        width: '1rem',
                      }}
                    />
                  ) : (
                    <FontAwesomeIcon
                      icon={faPlay}
                      style={{
                        width: '1rem',
                      }}
                    />
                  )}
                </button>
              </div>
            </form>
          </div>

          {/* Error Message */}
          {error && (
            <div
              className="alert alert-danger alert-dismissible fade show d-flex align-items-center"
              role="alert"
            >
              <FontAwesomeIcon
                icon={faExclamationTriangle}
                className="me-2"
              />

              <div>
                <strong>Error:</strong> {error}
              </div>

              <button
                type="button"
                className="btn-close"
                onClick={() => setError(null)}
              />
            </div>
          )}

          {/* Current Conversion */}
          {currentConversionItem && (
            <div className="mb-4">
              <DownloadItemCard
                item={currentConversionItem}
                showDismiss={false}
              />

            </div>
          )}

          {/* Recent Downloads */}
          {recentDownloads.length > 0 && (
            <div className="mt-4">
              <div className="d-flex align-items-center justify-content-between mb-3">
                <button
                  type="button"
                  className="btn btn-link text-dark text-decoration-none p-0 fw-semibold"
                  onClick={() =>
                    setShowRecentDownloads(
                      (current) => !current
                    )
                  }
                  aria-expanded={
                    showRecentDownloads
                  }
                >
                  <span className="me-2">
                    {showRecentDownloads
                      ? "▲"
                      : "▼"}
                  </span>

                  Recent Downloads
                </button>

                <button
                  type="button"
                  className="btn btn-sm btn-link text-muted text-decoration-none"
                  onClick={clearHistory}
                >
                  Clear
                </button>
              </div>

              {showRecentDownloads && (
                <div
                  className="d-flex flex-column gap-2"
                  style={{
                    maxHeight: '520px',
                    overflowY: 'auto',
                    overflowX: 'hidden',
                    paddingRight: '4px',
                  }}
                >
                  {recentDownloads.map(
                    (item) => (
                      <DownloadItemCard
                        key={item.id}
                        item={item}
                        showDismiss={true}
                      />
                    )
                  )}
                </div>
              )}
            </div>
          )}

        </div>
      </div>
    </div>
  );
}
