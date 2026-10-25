"use client";

import { generateId } from "ai";
import { type ReactNode, useState } from "react";

import Conversation from "@/components/Conversation";
import { MenuIcon } from "@/components/icons";
import Sidebar, { Logo } from "@/components/Sidebar";
import { rememberChat, useChats, useGeolocation, useTheme } from "@/components/stores";

type Props = { user: { name: string; image?: string | null; guest: boolean }; account: ReactNode };

/** The chat screen: sidebar with recent chats (kept in this browser) and the active conversation. */
export default function ChatApp({ user, account }: Props) {
  const chats = useChats();
  const [activeId, setActiveId] = useState(() => generateId());
  const [menuOpen, setMenuOpen] = useState(false);
  const [theme, setTheme] = useTheme();
  const geo = useGeolocation();
  const active = chats.find((chat) => chat.id === activeId);

  function select(id: string) {
    setActiveId(id);
    setMenuOpen(false);
  }

  return (
    <div className="flex h-dvh min-h-0 w-full">
      <Sidebar
        chats={chats}
        activeId={activeId}
        onSelect={select}
        onNew={() => select(generateId())}
        geo={geo}
        theme={theme}
        onTheme={setTheme}
        user={user}
        account={account}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-14 flex-none items-center gap-3 border-b border-border px-4 md:hidden">
          <button onClick={() => setMenuOpen(true)} aria-label="Open menu" className="rounded-full p-1.5 text-muted">
            <MenuIcon size={20} />
          </button>
          <Logo size={28} />
          <span className="font-display text-[17px]">Connoisseur</span>
        </div>
        <Conversation
          key={activeId}
          id={activeId}
          initialMessages={active?.messages ?? []}
          geo={geo}
          onSettled={(messages) => rememberChat(activeId, messages)}
        />
      </div>
    </div>
  );
}
