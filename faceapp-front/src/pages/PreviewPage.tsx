import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

const SOURCES = [
  { id: "p1", title: "Обложка",    link: "/illustr_1_1.jpg" },
  { id: "p2", title: "Страница 1", link: "/illustr_2_1.jpg" },
  { id: "p3", title: "Страница 2", link: "/illustr_3_1.jpg" },
];

// Desired mapping:
// cover: fairy_forest only
// page 1: underwater_mermaid
// page 2: superhero_city
const COVER_ID = "fairy_forest";
const PAGE_IDS: Record<string, string> = {
  p2: "underwater_mermaid",
  p3: "superhero_city",
};

export default function PreviewPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams(location.search);
  const jobId = params.get("job_id");
  
  const [activeId, setActiveId] = useState(SOURCES[0].id);
  const sectionRefs = useRef<Record<string, HTMLElement | null>>({});
  const [jobStatus, setJobStatus] = useState<string>("pending");
  const [results, setResults] = useState<{ id: string; url: string }[]>([]);
  const [statusMessage, setStatusMessage] = useState<string>("Загрузка...");
  const [error, setError] = useState<string | null>(null);
  const [faceDetected, setFaceDetected] = useState<boolean | null>(null);
  const [imagesLoaded, setImagesLoaded] = useState<Record<string, boolean>>({});
  
  const isProcessing = jobStatus !== "completed";

  const urlForId = (illId: string): string | null => {
    if (jobStatus !== "completed" || results.length === 0) return null;
    const found = results.find((r) => r.id === illId);
    return found ? found.url : null;
  };

  // Poll job status
  useEffect(() => {
    if (!jobId) {
      setError("Job ID not provided");
      return;
    }

    let intervalId: ReturnType<typeof setInterval>;

    const checkStatus = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/status/${jobId}`);
        if (!response.ok) throw new Error("Failed to check status");
        
        const data = await response.json();
        setJobStatus(data.status);
        if (typeof data.face_detected !== "undefined") {
          setFaceDetected(Boolean(data.face_detected));
        }

        // Update status message
        switch (data.status) {
          case "pending_analysis":
          case "analyzing":
            setStatusMessage("Анализируем фото ребенка...");
            break;
          case "analyzing_completed":
            setStatusMessage("Подготовка к генерации...");
            if (data.face_detected === false) {
              setError("На фото не обнаружено лицо. Пожалуйста, загрузите другое фото.");
            } else {
              setError(null);
            }
            break;
          case "pending_generation":
            setStatusMessage("Ожидание начала генерации...");
            break;
          case "generating":
            setStatusMessage("Генерируем изображение с перенесенным лицом...");
            break;
          case "completed":
            setStatusMessage("Готово!");
            // Fetch result URL
            const resultResponse = await fetch(`${API_BASE_URL}/result/${jobId}`);
            if (resultResponse.ok) {
              const resultData = await resultResponse.json();
              const items = Array.isArray(resultData.results)
                ? resultData.results
                : resultData.url
                ? [{ id: "default", url: resultData.url }]
                : [];
              setResults(items);
              // Reset loaded flags for incoming result images
              const nextLoaded: Record<string, boolean> = {};
              for (const it of items) nextLoaded[it.id] = false;
              setImagesLoaded(nextLoaded);
            }
            // Stop polling
            if (intervalId) clearInterval(intervalId);
            break;
          case "analysis_failed":
          case "generation_failed":
            setError("Произошла ошибка при обработке. Попробуйте еще раз.");
            if (intervalId) clearInterval(intervalId);
            break;
          default:
            setStatusMessage("Обработка...");
        }
      } catch (error) {
        console.error("Failed to check status:", error);
        setError("Ошибка при проверке статуса");
      }
    };

    // Check immediately
    checkStatus();

    // Then poll every 3 seconds
    intervalId = setInterval(checkStatus, 3000);

    return () => {
      if (intervalId) clearInterval(intervalId);
    };
  }, [jobId]);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => entries.forEach((e) => e.isIntersecting && setActiveId(e.target.id)),
      { threshold: 0.6 }
    );
    SOURCES.forEach(({ id }) => {
      const node = sectionRefs.current[id];
      if (node) observer.observe(node);
    });
    return () => observer.disconnect();
  }, []);

  const scrollTo = (id: string) => {
    const node = sectionRefs.current[id];
    if (!node) return;
    node.scrollIntoView({ behavior: "smooth", block: "start" });
    setActiveId(id);
  };

  const handleGenerate = async () => {
    if (!jobId) return;
    if (faceDetected === false) {
      setError("На фото не обнаружено лицо. Пожалуйста, загрузите другое фото.");
      return;
    }
    try {
      const formData = new FormData();
      formData.append("job_id", jobId);
      const resp = await fetch(`${API_BASE_URL}/generate/`, {
        method: "POST",
        body: formData,
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(data.message || data.detail || "Не удалось запустить генерацию");
      }
      const data = await resp.json();
      setJobStatus(data.status);
      setError(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Ошибка запуска генерации";
      setError(msg);
    }
  };

  return (
    <div className="min-h-screen bg-purple-50">
      <header className="bg-white/70 backdrop-blur">
        <div className="mx-auto max-w-7xl px-6 py-4 flex items-center justify-between">
          <h1 className="text-xl font-bold text-gray-900">Предпросмотр книги</h1>
          <div className="flex items-center gap-3">
            <button onClick={() => navigate('/')} className="px-4 py-2 rounded-lg bg-white text-gray-700 hover:bg-gray-50">Изменить данные</button>
            <button
              onClick={handleGenerate}
              disabled={!(jobStatus === 'analyzing_completed' && faceDetected === true)}
              className={`px-4 py-2 rounded-lg ${jobStatus === 'analyzing_completed' && faceDetected === true ? 'bg-purple-600 text-white hover:bg-purple-700' : 'bg-gray-200 text-gray-600 cursor-not-allowed'}`}
            >
              Сгенерировать
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">
        {error && (
          <div className="mb-6 rounded-lg border border-red-200 bg-red-50 text-red-800 px-4 py-3 text-sm">
            {error}
          </div>
        )}
        
        {isProcessing && !error && (
          <div className="mb-6 rounded-lg border border-purple-200 bg-purple-50 text-purple-800 px-4 py-3 text-sm flex items-center gap-3">
            <div className="w-5 h-5 border-2 border-purple-600 border-t-transparent rounded-full animate-spin" />
            <span>{statusMessage}</span>
          </div>
        )}
        
        {jobStatus === "completed" && results.length > 0 && (
          <div className="mb-6 rounded-lg border border-green-200 bg-green-50 text-green-800 px-4 py-3 text-sm flex items-center gap-3">
            <span className="material-icons text-green-600">check_circle</span>
            <span>{statusMessage} Изображение готово к просмотру.</span>
          </div>
        )}
        <div className="grid grid-cols-1 md:grid-cols-[240px_1fr] gap-8">
          <aside className="md:sticky md:top-6 self-start">
            <nav aria-label="Миниатюры страниц" className="space-y-4">
              {SOURCES.map((p) => {
                const illId = p.id === 'p1' ? COVER_ID : (PAGE_IDS as any)[p.id];
                const resultUrl = illId ? urlForId(illId) : null;
                const displayedSrc = jobStatus === 'completed' ? (resultUrl || p.link) : p.link;
                const shouldBlur = isProcessing || (jobStatus === 'completed' && (!resultUrl || (illId && !imagesLoaded[illId])));
                return (
                  <button
                    key={p.id}
                    onClick={() => scrollTo(p.id)}
                    className="w-full text-left group"
                  >
                    <div className="relative">
                      <img
                        src={displayedSrc}
                        alt={p.title}
                        className={`w-full h-auto rounded-lg ${shouldBlur ? "blur-sm" : ""}`}
                        loading="lazy"
                        onLoad={() => {
                          if (jobStatus === 'completed' && resultUrl && illId) {
                            setImagesLoaded((prev) => ({ ...prev, [illId]: true }));
                          }
                        }}
                      />
                      {isProcessing && (
                        <div className="absolute inset-0 rounded-lg bg-white/30" />
                      )}
                    </div>
                    <div className="mt-2 text-center text-sm">
                      <span className={activeId === p.id ? "font-semibold text-purple-700" : "font-semibold text-gray-800"}>
                        {p.title}
                      </span>
                    </div>
                  </button>
                );
              })}
            </nav>
          </aside>

          <section className="space-y-10">
            {SOURCES.map((p, index) => {
              const illId = p.id === 'p1' ? COVER_ID : (PAGE_IDS as any)[p.id];
              const resultUrl = illId ? urlForId(illId) : null;
              const displayedSrc = jobStatus === 'completed' ? (resultUrl || p.link) : p.link;
              const shouldBlur = isProcessing || (jobStatus === 'completed' && (!resultUrl || (illId && !imagesLoaded[illId])));
              const imgClassBase = index === 0 || p.id === 'p1' ? 'mx-auto w-1/2 h-auto' : 'w-full h-auto';
              return (
                <article
                  id={p.id}
                  key={p.id}
                  ref={(el: HTMLElement | null) => {
                    if (el) sectionRefs.current[p.id] = el;
                  }}
                >
                  <h2 className="mb-2 text-lg font-semibold text-gray-900">{p.title}</h2>
                  <div className="relative">
                    <img
                      src={displayedSrc}
                      alt={p.title}
                      className={`${imgClassBase} ${shouldBlur ? "blur-md" : ""}`}
                      loading="lazy"
                      onLoad={() => {
                        if (jobStatus === 'completed' && resultUrl && illId) {
                          setImagesLoaded((prev) => ({ ...prev, [illId]: true }));
                        }
                      }}
                    />
                    {isProcessing && !error && (
                      <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                        <div className="rounded-full border-2 border-purple-600/30 border-t-purple-600 w-8 h-8 animate-spin" />
                      </div>
                    )}
                  </div>
                </article>
              );
            })}
          </section>
        </div>
      </main>
    </div>
  );
}


