"use client";

import Link from "next/link";
import { CalendarDays, MapPin, Users } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { useI18n } from "@/lib/i18n";
import type { TripPost } from "@/lib/types";

export default function TripCard({ post }: { post: TripPost }) {
  const { t, label, formatDate } = useI18n();
  const start = formatDate(post.start_date);
  const end = formatDate(post.end_date);

  return (
    <Link href={`/trips/${post.id}`} className="block">
      <Card className="h-full transition-shadow hover:shadow-md">
        <CardHeader className="pb-3">
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <Badge variant="accent">{label("budget", post.budget_type)}</Badge>
            {post.tags.slice(0, 2).map((tag) => (
              <Badge key={tag} variant="secondary">
                {tag}
              </Badge>
            ))}
          </div>
          <CardTitle className="line-clamp-2 text-base">{post.title}</CardTitle>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-1 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <MapPin className="h-3.5 w-3.5" />
              {post.destination_city ? `${post.destination_city}, ` : ""}
              {post.destination_country}
            </span>
            {(start || end) && (
              <span className="inline-flex items-center gap-1">
                <CalendarDays className="h-3.5 w-3.5" />
                {start}
                {end ? ` – ${end}` : ""}
              </span>
            )}
            <span className="inline-flex items-center gap-1">
              <Users className="h-3.5 w-3.5" />
              {t("tripCard.lookingFor", { count: post.looking_for_count })}
            </span>
          </div>
        </CardHeader>

        <CardContent className="pt-0">
          <p className="line-clamp-2 text-sm text-muted-foreground">{post.description}</p>

          {post.creator && (
            <div className="mt-4 flex items-center gap-2">
              <Avatar className="h-7 w-7">
                {post.creator.avatar_url && <AvatarImage src={post.creator.avatar_url} alt="" />}
                <AvatarFallback className="text-xs">
                  {post.creator.nickname.slice(0, 1)}
                </AvatarFallback>
              </Avatar>
              <span className="text-xs font-medium">{post.creator.nickname}</span>
              {post.creator.mbti && (
                <Badge variant="outline" className="ml-auto text-[10px]">
                  {post.creator.mbti}
                </Badge>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </Link>
  );
}
