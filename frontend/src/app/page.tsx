"use client";

import Link from "next/link";
import { CalendarCheck, MessageSquare, ShieldCheck, Sparkles } from "lucide-react";

import { useT } from "@/lib/i18n";
import type { MessageKey } from "@/lib/i18n/dictionaries";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * A client component purely so the copy can be translated. It holds no state and
 * fetches nothing, so the rendered HTML is unchanged — Next still prerenders it
 * at build time, in the default locale.
 */

const FEATURES: { icon: typeof Sparkles; title: MessageKey; body: MessageKey }[] = [
  { icon: Sparkles, title: "home.feature.match.title", body: "home.feature.match.body" },
  { icon: MessageSquare, title: "home.feature.chat.title", body: "home.feature.chat.body" },
  { icon: ShieldCheck, title: "home.feature.privacy.title", body: "home.feature.privacy.body" },
  { icon: CalendarCheck, title: "home.feature.review.title", body: "home.feature.review.body" },
];

const STATS: { value: MessageKey; label: MessageKey }[] = [
  { value: "home.stat.steps.value", label: "home.stat.steps.label" },
  { value: "home.stat.pdpo.value", label: "home.stat.pdpo.label" },
  { value: "home.stat.rate.value", label: "home.stat.rate.label" },
];

export default function HomePage() {
  const t = useT();

  return (
    <div className="space-y-12">
      <section className="pt-10">
        <Badge variant="secondary">{t("home.badge")}</Badge>
        <h1 className="mt-4 text-4xl font-extrabold leading-tight tracking-tight sm:text-5xl">
          {t("home.titleLine1")}
          <br />
          {t("home.titleLine2")}
        </h1>
        <p className="mt-4 max-w-2xl text-base text-muted-foreground">{t("home.intro")}</p>

        <div className="mt-7 flex flex-wrap gap-3">
          <Button asChild size="lg">
            <Link href="/register">{t("home.ctaStart")}</Link>
          </Button>
          <Button asChild size="lg" variant="outline">
            <Link href="/trips">{t("home.ctaBrowse")}</Link>
          </Button>
        </div>

        <dl className="mt-10 flex flex-wrap gap-x-12 gap-y-4">
          {STATS.map(({ value, label }) => (
            <div key={label}>
              <dt className="text-2xl font-bold">{t(value)}</dt>
              <dd className="text-sm text-muted-foreground">{t(label)}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="grid gap-4 sm:grid-cols-2">
        {FEATURES.map(({ icon: Icon, title, body }) => (
          <Card key={title}>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <Icon className="h-4 w-4 text-primary" />
                {t(title)}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">{t(body)}</p>
            </CardContent>
          </Card>
        ))}
      </section>
    </div>
  );
}
