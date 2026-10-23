"use client";

import type { ReactNode } from "react";

import { CloseIcon, PinIcon, PlusIcon } from "@/components/icons";
import type { Geolocation, Theme } from "@/components/stores";
import type { StoredChat } from "@/lib/chats";

type Props = {
  chats: StoredChat[];
  activeId: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  geo: Geolocation;
  theme: Theme;
  onTheme: (theme: Theme) => void;
  user: { name: string; image?: string | null; guest: boolean };
  account: ReactNode;
  open: boolean;
  onClose: () => void;
};

/** Left rail from the design: brand, new chat, recent chats, location status and the light/dark switch. */
export default function Sidebar(props: Props) {
  const { chats, activeId, onSelect, onNew, geo, theme, onTheme, user, account, open, onClose } = props;
  return (
    <>
      {open && (
        <button aria-label="Close menu" onClick={onClose} className="fixed inset-0 z-30 bg-black/30 md:hidden" />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-64 flex-none flex-col gap-[18px] border-r border-border bg-surface px-4 py-[22px] transition-transform md:static md:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center gap-2.5 px-1.5">
          <Logo />
          <span className="font-display text-[19px]">Connoisseur</span>
          <button onClick={onClose} aria-label="Close menu" className="ml-auto rounded-full p-1.5 text-muted md:hidden">
            <CloseIcon size={18} />
          </button>
        </div>

        <button
          onClick={onNew}
          className="flex h-11 flex-none items-center justify-center gap-2 rounded-full bg-accent text-sm font-semibold text-on-accent hover:opacity-90"
        >
          <PlusIcon size={16} />
          New chat
        </button>

        <nav aria-label="Recent chats" className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
          <div className="px-2.5 pb-1.5 text-[11px] font-semibold uppercase tracking-[.08em] text-muted">Recent</div>
          {chats.length === 0 && <p className="px-3 text-sm text-muted">Your chats will appear here.</p>}
          {chats.map((chat) => {
            const active = chat.id === activeId;
            return (
              <button
                key={chat.id}
                onClick={() => onSelect(chat.id)}
                aria-current={active ? "page" : undefined}
                className={`flex-none truncate rounded-xl px-3 py-[9px] text-left text-sm ${
                  active ? "bg-accent-soft font-semibold text-accent-text" : "text-foreground hover:bg-surface-2"
                }`}
              >
                {chat.title}
              </button>
            );
          })}
        </nav>

        <div className="flex flex-col gap-3">
          <LocationStatus geo={geo} />

          <div role="group" aria-label="Theme" className="grid grid-cols-2 rounded-full bg-surface-2 p-1">
            {(["light", "dark"] as const).map((value) => (
              <button
                key={value}
                onClick={() => onTheme(value)}
                aria-pressed={theme === value}
                className={`h-[34px] rounded-full text-[13px] font-semibold capitalize text-foreground ${
                  theme === value ? "bg-seg-active shadow-sm" : ""
                }`}
              >
                {value}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2 px-1 text-[13px] text-muted">
            {user.image ? (
              // eslint-disable-next-line @next/next/no-img-element -- GitHub avatar, external URL
              <img src={user.image} alt="" className="h-6 w-6 rounded-full" />
            ) : null}
            <span className="min-w-0 flex-1 truncate">{user.guest ? `${user.name} (dev)` : user.name}</span>
            {account}
          </div>
        </div>
      </aside>
    </>
  );
}

export function Logo({ size = 34 }: { size?: number }) {
  return (
    <div
      className="grid flex-none place-items-center rounded-full bg-accent font-display text-on-accent"
      style={{ width: size, height: size, fontSize: Math.round(size * 0.53) }}
      aria-hidden="true"
    >
      C
    </div>
  );
}

function LocationStatus({ geo }: { geo: Geolocation }) {
  if (geo.location) {
    return (
      <div className="flex items-center gap-2.5 rounded-2xl border border-accent-soft bg-accent-soft p-3 text-accent-text">
        <PinIcon size={18} className="flex-none" />
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="text-[13px] font-semibold">
            Sharing · {geo.location.lat.toFixed(2)}, {geo.location.lon.toFixed(2)}
          </span>
          <span className="text-xs">Rounded to ~1 km</span>
        </div>
        <button onClick={geo.stop} className="text-xs font-semibold underline">
          Stop
        </button>
      </div>
    );
  }
  const detail =
    geo.status === "asking"
      ? "Waiting for permission…"
      : geo.status === "denied"
        ? "Not available. Type your city instead."
        : "I'll ask where you are";
  return (
    <button
      onClick={() => void geo.request()}
      disabled={geo.status === "asking"}
      title="Share my location"
      className="flex items-center gap-2.5 rounded-2xl border border-border bg-background p-3 text-left hover:border-accent"
    >
      <PinIcon size={18} className="flex-none text-muted" />
      <span className="flex flex-col">
        <span className="text-[13px] font-semibold">Location off</span>
        <span className="text-xs text-muted">{detail}</span>
      </span>
    </button>
  );
}
