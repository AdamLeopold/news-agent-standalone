import type {ReactNode, ComponentType, PropsWithChildren} from 'react';
export type Language = 'zh' | 'en';
export const translations: Record<string, string>;
export function translateText(value: string, language?: Language): string;
export function translateNode(node: ReactNode, language: Language): ReactNode;
export const OriginalText: ComponentType<PropsWithChildren<{language?: string}>>;
export function savedLanguage(storage?: {getItem(key: string): string | null}): Language;
