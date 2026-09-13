import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { profileStorage } from "./profileStorage";

// Non-secret preferences shared by every Paper reader. Connections live in OAW.
export const useLibrarySettings = create(persist(() => ({
  provider: "openai",
  model: "",
  target: "简体中文",
}), { name: "oaw-library-preferences", storage: createJSONStorage(() => profileStorage) }));
