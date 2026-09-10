import { create } from "zustand";
import { persist } from "zustand/middleware";

// Non-secret preferences shared by every Paper reader. Connections live in OAW.
export const useLibrarySettings = create(persist(() => ({
  provider: "openai",
  model: "",
  target: "简体中文",
}), { name: "oaw-library-preferences" }));
