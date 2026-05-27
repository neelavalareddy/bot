'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/cjs/styles/prism';
import { useState } from 'react';
import type { Message } from './ChatInterface';
import UiBlock from './UiBlock';

// Keep only the last progress block so earlier ones don't pile up
function dedupeProgress(content: string): string {
  const re = /```ui\n(\{"type":"progress"[\s\S]*?)\n```/g;
  const all = [...content.matchAll(re)];
  if (all.length <= 1) return content;
  let out = content;
  for (let i = 0; i < all.length - 1; i++) {
    out = out.replace(all[i][0], '');
  }
  return out;
}

interface Props {
  message: Message;
  streaming?: boolean;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className="absolute top-2 right-2 text-xs text-gray-400 hover:text-white bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded opacity-0 group-hover:opacity-100 transition-all"
    >
      {copied ? '✓ Copied' : 'Copy'}
    </button>
  );
}

export default function MessageBubble({ message, streaming }: Props) {
  const isUser = message.role === 'user';

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-2xl ml-16 bg-blue-600 text-white rounded-2xl px-4 py-3 text-sm">
          <p className="whitespace-pre-wrap">{message.content}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-3xl mr-8 w-full">
        <div className="flex items-center gap-2 mb-1 ml-1">
          <span className="text-xs text-gray-500 font-medium">Universal Bot</span>
          {streaming && (
            <span className="text-xs text-blue-400 animate-pulse">● thinking</span>
          )}
        </div>
        <div className="bg-gray-800 rounded-2xl px-4 py-3">
          <div className="prose prose-invert prose-sm max-w-none
            prose-headings:text-gray-100
            prose-p:text-gray-200 prose-p:leading-relaxed
            prose-strong:text-gray-100
            prose-code:text-pink-300 prose-code:bg-gray-700 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs prose-code:font-mono prose-code:before:content-none prose-code:after:content-none
            prose-blockquote:border-gray-600 prose-blockquote:text-gray-400
            prose-li:text-gray-200
            prose-a:text-blue-400 prose-a:no-underline hover:prose-a:underline
            prose-hr:border-gray-700
            prose-table:text-sm prose-th:text-gray-300 prose-td:text-gray-300 prose-thead:border-gray-600 prose-tbody:divide-gray-700">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                code({ node, inline, className, children, ...props }: any) {
                  const match = /language-(\w+)/.exec(className ?? '');
                  if (!inline && match) {
                    if (match[1] === 'ui') {
                      return <UiBlock raw={String(children)} />;
                    }
                    return (
                      <div className="relative group my-3 rounded-lg overflow-hidden">
                        <SyntaxHighlighter
                          style={oneDark}
                          language={match[1]}
                          PreTag="div"
                          customStyle={{ margin: 0, borderRadius: '0.5rem', fontSize: '0.8rem' }}
                        >
                          {String(children).replace(/\n$/, '')}
                        </SyntaxHighlighter>
                        <CopyButton text={String(children)} />
                      </div>
                    );
                  }
                  return (
                    <code className={className} {...props}>
                      {children}
                    </code>
                  );
                },
                pre({ children }) {
                  return <>{children}</>;
                },
                a({ href, children }) {
                  return (
                    <a href={href} target="_blank" rel="noopener noreferrer">
                      {children}
                    </a>
                  );
                },
              }}
            >
              {dedupeProgress(message.content)}
            </ReactMarkdown>
          </div>
        </div>
      </div>
    </div>
  );
}
