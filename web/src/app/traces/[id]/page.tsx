/* Trace detail route. The export only pre-renders the /traces/detail exemplar
 * (generateStaticParams below); the deep-link fallback in
 * layawatch/http/static.py serves that exported page for any /traces/<id>, and
 * the client component reads the real id from the URL. */
import TraceDetail from "./TraceDetail";

export function generateStaticParams(): Array<{ id: string }> {
  return [{ id: "detail" }];
}

export default function TraceDetailPage() {
  return <TraceDetail />;
}
