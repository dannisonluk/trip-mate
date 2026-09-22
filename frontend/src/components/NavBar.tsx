"use client";

import Link from "next/link";
import { useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  Check,
  Compass,
  Languages,
  LogOut,
  MessageSquare,
  PlusCircle,
  ShieldAlert,
  User,
} from "lucide-react";

import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { LOCALES, LOCALE_LABEL, type MessageKey } from "@/lib/i18n/dictionaries";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import NotificationBell from "@/components/NotificationBell";

const LINKS: { href: string; key: MessageKey; icon: typeof Compass }[] = [
  { href: "/trips", key: "nav.trips", icon: Compass },
  { href: "/trips/new", key: "nav.newTrip", icon: PlusCircle },
  { href: "/chat", key: "nav.chat", icon: MessageSquare },
  { href: "/profile", key: "nav.profile", icon: User },
];

/**
 * Language picker.
 *
 * Always rendered, including when signed out: someone who cannot read the
 * default language needs this *before* they can register, not after.
 *
 * The trigger carries an explicit `aria-label` so its accessible name is stable
 * regardless of which language is currently displayed — the E2E suite and any
 * screen-reader user should be able to address it the same way every time.
 */
function LocaleSwitcher() {
  const { locale, setLocale, t } = useI18n();
  // Controlled so the menu dismisses once a choice is made. Radix does not
  // close a Popover on a plain button click, and a language menu that stays
  // open after you have used it reads as a broken control.
  const [open, setOpen] = useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-1.5" aria-label={t("nav.language")}>
          <Languages className="h-4 w-4" />
          <span className="hidden sm:inline">{LOCALE_LABEL[locale]}</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-44 p-1">
        {LOCALES.map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => {
              setLocale(option);
              setOpen(false);
            }}
            className={cn(
              "flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-sm transition-colors hover:bg-muted",
              option === locale && "font-semibold",
            )}
          >
            {LOCALE_LABEL[option]}
            {option === locale && <Check className="h-3.5 w-3.5" />}
          </button>
        ))}
      </PopoverContent>
    </Popover>
  );
}

export default function NavBar() {
  const { user, loading, logout } = useAuth();
  const { t } = useI18n();
  const pathname = usePathname();
  const router = useRouter();
  // The admin link is only a convenience — the API enforces the role. Showing
  // it to everyone would just advertise an endpoint that 403s.
  const isAdmin = user?.role === "ADMIN";

  async function handleLogout() {
    await logout();
    router.push("/");
  }

  return (
    <header className="sticky top-0 z-40 border-b bg-card/85 backdrop-blur">
      <div className="container flex h-16 items-center justify-between">
        <Link href="/" className="flex items-center gap-2.5 font-extrabold tracking-tight">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-primary to-accent text-sm text-white">
            TM
          </span>
          <span className="text-lg">Trip Mate</span>
        </Link>

        <nav className="flex items-center gap-1">
          {user && !loading ? (
            <>
              {LINKS.map(({ href, key, icon: Icon }) => {
                const active = pathname === href;
                return (
                  <Button
                    key={href}
                    asChild
                    variant={active ? "secondary" : "ghost"}
                    size="sm"
                    className={cn("gap-1.5", active && "text-foreground")}
                  >
                    <Link href={href}>
                      <Icon className="h-4 w-4" />
                      <span className="hidden sm:inline">{t(key)}</span>
                    </Link>
                  </Button>
                );
              })}
              {isAdmin && (
                <Button
                  asChild
                  variant={pathname.startsWith("/admin") ? "secondary" : "ghost"}
                  size="sm"
                  className="gap-1.5"
                >
                  <Link href="/admin">
                    <ShieldAlert className="h-4 w-4" />
                    <span className="hidden sm:inline">{t("nav.admin")}</span>
                  </Link>
                </Button>
              )}
              <NotificationBell />
              <Button variant="ghost" size="sm" onClick={handleLogout} className="gap-1.5">
                <LogOut className="h-4 w-4" />
                <span className="hidden sm:inline">{t("nav.logout")}</span>
              </Button>
            </>
          ) : (
            !loading && (
              <>
                <Button asChild variant="ghost" size="sm">
                  <Link href="/login">{t("nav.login")}</Link>
                </Button>
                <Button asChild size="sm">
                  <Link href="/register">{t("nav.register")}</Link>
                </Button>
              </>
            )
          )}
          <LocaleSwitcher />
        </nav>
      </div>
    </header>
  );
}
