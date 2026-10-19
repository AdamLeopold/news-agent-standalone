import {createContext, useContext, type PropsWithChildren} from 'react';
import {translateNode, type Language} from './i18n.mjs';

const LanguageContext = createContext<Language>('zh');
export function useLanguage() {return useContext(LanguageContext);}

export function LanguageProvider({language, children}: PropsWithChildren<{language: Language}>) {
  return <LanguageContext.Provider value={language}>{children}</LanguageContext.Provider>;
}

export function Localized({children}: PropsWithChildren) {
  const language = useContext(LanguageContext);
  return <>{translateNode(children, language)}</>;
}
