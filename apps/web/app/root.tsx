import fontStylesheet from "@fontsource-variable/noto-sans-jp/index.css?url";
import {
  Links,
  Meta,
  Outlet,
  Scripts,
  ScrollRestoration,
  isRouteErrorResponse,
  useRouteError,
} from "react-router";

import type { Route } from "./+types/root";
import stylesheet from "./styles/app.css?url";

export const links: Route.LinksFunction = () => [
  { rel: "stylesheet", href: fontStylesheet },
  { rel: "stylesheet", href: stylesheet },
];

export const meta: Route.MetaFunction = () => [
  { title: "Stroke Evidence Lab" },
  {
    name: "description",
    content: "脳梗塞の創薬仮説を公開論文から調査するローカル研究支援ツール",
  },
];

export function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja">
      <head>
        <meta charSet="utf-8" />
        <meta content="width=device-width, initial-scale=1" name="viewport" />
        <Meta />
        <Links />
      </head>
      <body>
        {children}
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

export default function App() {
  return <Outlet />;
}

export function ErrorBoundary() {
  const error = useRouteError();
  const status = isRouteErrorResponse(error) ? error.status : 500;
  const message = isRouteErrorResponse(error)
    ? error.statusText || "ページを表示できません。"
    : "予期しないエラーが発生しました。";

  return (
    <main className="fatal-error">
      <p>{status}</p>
      <h1>{message}</h1>
      <a href="/">新しい調査へ戻る</a>
    </main>
  );
}
