import React, { useRef, useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export default function PersonalizationPage() {
  const navigate = useNavigate();
  const [nameValue, setNameValue] = useState("");
  const [ageValue, setAgeValue] = useState("");
  const [genderValue, setGenderValue] = useState<"boy" | "girl">("boy");
  const [isUploading, setIsUploading] = useState(false);

  const [photoDataUrl, setPhotoDataUrl] = useState<string | null>(null);
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [photoName, setPhotoName] = useState("");
  const [photoSize, setPhotoSize] = useState(0);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const dropRef = useRef<HTMLDivElement | null>(null);

  const MAX_SIZE_MB = 10;
  const acceptTypes = ["image/jpeg", "image/png", "image/webp"];

  const photoReady = !!photoDataUrl;
  const isPreviewEnabled = photoReady && nameValue.trim() && ageValue.trim() && !isUploading;

  const triggerFileDialog = () => fileInputRef.current?.click();

  // Removed illustrations fetch: input page shows only upload and form

  const handleFile = (file: File) => {
    if (!acceptTypes.includes(file.type)) {
      alert("Поддерживаются только JPG / PNG / WEBP");
      return;
    }
    if (file.size > MAX_SIZE_MB * 1024 * 1024) {
      alert(`Файл больше ${MAX_SIZE_MB} МБ`);
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setPhotoDataUrl(String(reader.result));
      setPhotoName(file.name);
      setPhotoSize(file.size);
      setPhotoFile(file);
    };
    reader.readAsDataURL(file);
  };

  const onInputFileChange: React.ChangeEventHandler<HTMLInputElement> = (e) => {
    const f = e.target.files?.[0];
    if (f) handleFile(f);
  };

  useEffect(() => {
    const dz = dropRef.current;
    if (!dz) return;

    const onDrag = (e: DragEvent) => {
      e.preventDefault();
      dz.classList.add("ring-2", "ring-purple-400");
    };
    const onLeave = (e: DragEvent) => {
      e.preventDefault();
      dz.classList.remove("ring-2", "ring-purple-400");
    };
    const onDrop = (e: DragEvent) => {
      e.preventDefault();
      dz.classList.remove("ring-2", "ring-purple-400");
      const file = e.dataTransfer?.files?.[0];
      if (file) handleFile(file);
    };

    dz.addEventListener("dragenter", onDrag);
    dz.addEventListener("dragover", onDrag);
    dz.addEventListener("dragleave", onLeave);
    dz.addEventListener("drop", onDrop);

    return () => {
      dz.removeEventListener("dragenter", onDrag);
      dz.removeEventListener("dragover", onDrag);
      dz.removeEventListener("dragleave", onLeave);
      dz.removeEventListener("drop", onDrop);
    };
  }, []);

  const handleRemovePhoto = () => {
    setPhotoDataUrl(null);
    setPhotoName("");
    setPhotoSize(0);
    setPhotoFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const openPreview = async () => {
    if (!isPreviewEnabled || !photoFile) return;
    
    setIsUploading(true);
    try {
      // Upload and analyze photo
      const formData = new FormData();
      formData.append("child_photo", photoFile);
      // illustration_id is optional on backend; omit to generate across all illustrations
      formData.append("child_name", nameValue.trim());
      formData.append("child_age", ageValue.trim());
      formData.append("child_gender", genderValue);

      const response = await fetch(`${API_BASE_URL}/upload_and_analyze/`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || "Failed to upload photo");
      }

      const data = await response.json();
      const jobId = data.job_id;

      // Navigate to preview page with job_id
      navigate(`/preview?job_id=${jobId}`);
    } catch (error) {
      console.error("Failed to upload photo:", error);
      alert(`Ошибка при загрузке фото: ${error instanceof Error ? error.message : "Неизвестная ошибка"}`);
    } finally {
      setIsUploading(false);
    }
  };

  const sizeKB = Math.round(photoSize / 1024);

  return (
    <div className="bg-purple-50 min-h-screen">
      <div className="container mx-auto p-8">
        <div className="max-w-7xl mx-auto">
          <div className="mb-12">
            <div className="relative flex justify-between items-center w-full">
              <div className="absolute top-1/2 left-0 right-0 h-0.5 bg-gray-200 -z-10" />
              <div className="absolute top-1/2 left-0 h-0.5 bg-purple-500 -z-10" style={{ width: "25%" }} />

              <div className="flex items-center gap-2 bg-white pr-4">
                <div className="w-6 h-6 rounded-full flex items-center justify-center text-white bg-purple-500">
                  <span className="material-icons text-sm">done</span>
                </div>
                <span className="text-purple-700 font-semibold">Книга</span>
              </div>

              <div className="flex items-center gap-2 bg-white pr-4 pl-4">
                <div className="w-6 h-6 rounded-full flex items-center justify-center text-white bg-purple-500">
                  <span className="material-icons text-sm" style={{ fontSize: 16 }}>child_care</span>
                </div>
                <span className="text-purple-700 font-semibold">Информация о ребенке</span>
              </div>

              <div className="flex items-center gap-2 bg-white pr-4 pl-4">
                <div className="w-6 h-6 rounded-full bg-white border-2 border-gray-300" />
                <span className="text-gray-500">Предпросмотр</span>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-12">
            <div className="bg-white p-8 rounded-lg shadow-md h-full">
              <h2 className="text-2xl font-bold mb-6">Начать персонализацию</h2>

              {!photoDataUrl ? (
                <div className="border border-dashed border-purple-300 rounded-lg p-6 mb-6">
                  <div className="text-center mb-4">
                    <span className="text-purple-700 font-semibold">СОВЕТЫ</span>
                  </div>

                  <div className="flex justify-center gap-4 mb-4">
                    {Array.from({ length: 3 }).map((_, i) => (
                      <div className="relative" key={`bad-${i}`}>
                        <div className="w-16 h-16 rounded-full bg-gray-200" />
                        <div className="absolute -top-1 -right-1 w-5 h-5 rounded-full bg-white shadow flex items-center justify-center">
                          <span className="material-icons text-red-500 text-sm">close</span>
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="flex justify-center gap-4 mb-6">
                    {Array.from({ length: 3 }).map((_, i) => (
                      <div className="relative" key={`good-${i}`}>
                        <div className="w-16 h-16 rounded-full bg-gray-200" />
                        <div className="absolute -top-1 -right-1 w-5 h-5 rounded-full bg-white shadow flex items-center justify-center">
                          <span className="material-icons text-green-600 text-sm">done</span>
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="flex flex-col items-center gap-3" ref={dropRef}>
                    <button
                      type="button"
                      className="bg-purple-600 text-white font-semibold py-2 px-6 rounded-lg hover:bg-purple-700 transition duration-300 flex items-center"
                      onClick={triggerFileDialog}
                    >
                      Выбрать изображение
                      <span className="material-icons ml-2">upload</span>
                    </button>
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      className="hidden"
                      onChange={onInputFileChange}
                    />
                    <p className="text-xs text-gray-400">Можно перетащить файл мышкой в эту область</p>
                  </div>
                </div>
              ) : (
                <div className="border border-dashed border-purple-300 rounded-lg p-0 mb-6">
                  <div className="relative rounded-lg overflow-hidden">
                    <img src={photoDataUrl!} alt="Предпросмотр" className="w-full h-80 object-cover select-none" />
                    <button
                      type="button"
                      aria-label="Удалить фото"
                      onClick={handleRemovePhoto}
                      className="absolute top-3 right-3 w-10 h-10 rounded-full bg-white shadow-md flex items-center justify-center focus:outline-none focus:ring-2 focus:ring-purple-400"
                    >
                      <span className="material-icons text-gray-700">close</span>
                    </button>
                    <div className="absolute bottom-0 left-0 right-0 bg-black/40 text-white px-4 py-2 text-sm flex items-center justify-between">
                      <span className="truncate">{photoName}</span>
                      <span className="opacity-90">~ {sizeKB} KB</span>
                    </div>
                  </div>
                </div>
              )}

              <form onSubmit={(e) => e.preventDefault()}>
                <div className="grid grid-cols-2 gap-4 mb-4">
                  <div>
                    <label htmlFor="child-first-name" className="block text-sm font-medium text-gray-700 mb-1">
                      Имя ребенка
                    </label>
                    <input
                      id="child-first-name"
                      name="child-first-name"
                      type="text"
                      value={nameValue}
                      onChange={(e) => setNameValue(e.target.value)}
                      className="w-full border border-gray-300 rounded-lg p-2 focus:ring-purple-500 focus:border-purple-500"
                      placeholder="Введите имя"
                    />
                  </div>
                  <div>
                    <label htmlFor="child-age" className="block text-sm font-medium text-gray-700 mb-1">
                      Возраст ребенка
                    </label>
                    <input
                      id="child-age"
                      name="child-age"
                      type="number"
                      min="0"
                      max="18"
                      value={ageValue}
                      onChange={(e) => setAgeValue(e.target.value)}
                      className="w-full border border-gray-300 rounded-lg p-2 focus:ring-purple-500 focus:border-purple-500"
                      placeholder="Возраст"
                    />
                  </div>
                </div>

                <div className="mb-6">
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Пол ребенка
                  </label>
                  <div className="flex gap-4">
                    <label className="flex items-center cursor-pointer">
                      <input
                        type="radio"
                        name="gender"
                        value="boy"
                        checked={genderValue === "boy"}
                        onChange={(e) => setGenderValue(e.target.value as "boy" | "girl")}
                        className="mr-2"
                      />
                      <span className="text-gray-700">Мальчик</span>
                    </label>
                    <label className="flex items-center cursor-pointer">
                      <input
                        type="radio"
                        name="gender"
                        value="girl"
                        checked={genderValue === "girl"}
                        onChange={(e) => setGenderValue(e.target.value as "boy" | "girl")}
                        className="mr-2"
                      />
                      <span className="text-gray-700">Девочка</span>
                    </label>
                  </div>
                </div>

                <div className="mt-6">
                  <button
                    type="button"
                    onClick={openPreview}
                    disabled={!isPreviewEnabled}
                    className={`w-full font-semibold py-3 px-6 rounded-lg transition duration-300 flex items-center justify-center gap-2 ${
                      isPreviewEnabled
                        ? "bg-purple-600 text-white hover:bg-purple-700 cursor-pointer"
                        : "bg-gray-200 text-gray-700 cursor-not-allowed"
                    }`}
                  >
                    {isUploading ? (
                      <>
                        <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                        Загрузка...
                      </>
                    ) : (
                      "Сгенерить"
                    )}
                  </button>
                </div>
              </form>
            </div>

            {/* Right-side illustrations panel removed */}
          </div>
        </div>
      </div>
    </div>
  );
}


