/**
 * Processing recipes (APP_API.md "M3 phase 2"): named stage settings plus edits that apply in
 * one click. Built-ins come first and can't be deleted; "Save as recipe" stores a scan's steps.
 */
import { create } from "zustand";
import { ApiError, api } from "../api/http";
import type { NewRecipeBody, Recipe } from "../api/types";
import { toast } from "./toasts";

type RecipesStore = {
  list: Recipe[] | null;
  loading: boolean;
  error: ApiError | null;
  fetch: () => Promise<void>;
  /** Re-read the list if it was ever loaded (after a reconnect). */
  refetch: () => void;
  create: (body: NewRecipeBody) => Promise<Recipe | null>;
  remove: (recipe: Recipe) => Promise<boolean>;
};

const asApiError = (e: unknown) => (e instanceof ApiError ? e : new ApiError(0, e instanceof Error ? e.message : String(e), null, "/api/recipes"));

/** Built-ins first (the engine's order within each group). */
const ordered = (list: Recipe[]) => [...list.filter((r) => r.builtin), ...list.filter((r) => !r.builtin)];

export const useRecipes = create<RecipesStore>((set, get) => ({
  list: null,
  loading: false,
  error: null,

  fetch: async () => {
    if (get().loading) return;
    set({ loading: true });
    try {
      const list = await api.recipes();
      set({ list: ordered(list), loading: false, error: null });
    } catch (e) {
      set({ loading: false, error: asApiError(e) });
    }
  },

  refetch: () => {
    if (get().list || get().error) void get().fetch();
  },

  create: async (body) => {
    try {
      const r = await api.createRecipe(body);
      set((s) => ({ list: ordered([...(s.list ?? []).filter((x) => x.id !== r.id), r]) }));
      return r;
    } catch (e) {
      toast.error("Couldn't save the recipe", e);
      return null;
    }
  },

  remove: async (recipe) => {
    try {
      await api.deleteRecipe(recipe.id);
      set((s) => ({ list: s.list ? s.list.filter((x) => x.id !== recipe.id) : s.list }));
      return true;
    } catch (e) {
      toast.error(`Couldn't delete “${recipe.name}”`, e);
      return false;
    }
  },
}));
