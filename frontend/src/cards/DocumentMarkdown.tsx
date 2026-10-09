import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Read imported documents without executing HTML or loading remote images. */
export function DocumentMarkdown({ content }: { content: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
    img: ({ alt }) => <span>{alt}</span>,
    a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
  }}>{content}</ReactMarkdown>;
}
