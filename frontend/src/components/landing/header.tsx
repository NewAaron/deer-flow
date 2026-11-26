import Image from "next/image";
import Link from "next/link";
import { type ReactNode } from "react";

import { DEFAULT_LOCALE, type Locale } from "@/core/i18n/locale";
import { getI18n } from "@/core/i18n/server";
import { cn } from "@/lib/utils";

import { MobileNav } from "./mobile-nav";

export type HeaderProps = {
  className?: string;
  homeURL?: string;
  locale?: Locale;
  /** Optional search control, e.g. the docs layout's Pagefind search. */
  search?: ReactNode;
};

export async function Header({
  className,
  homeURL,
  locale,
  search,
}: HeaderProps) {
  const { locale: resolvedLocale, t } = await getI18n(locale ?? DEFAULT_LOCALE);
  const lang = resolvedLocale.substring(0, 2);
  return (
    <header
      className={cn(
        "container-md fixed top-0 right-0 left-0 z-20 mx-auto flex h-16 items-center justify-between gap-3 px-4 backdrop-blur-xs",
        className,
      )}
    >
      <div className="flex min-w-0 items-center gap-6">
        <Link
          href={homeURL ?? "/"}
          className="flex items-center gap-2 font-serif text-xl whitespace-nowrap"
        >
          <Image
            src="/cvci/emblem.png"
            width={32}
            height={32}
            alt=""
            unoptimized
            className="size-8 object-contain"
          />
          MSAI
        </Link>
      </div>
      <nav className="ml-auto hidden items-center gap-5 text-sm font-medium sm:flex md:mr-8 md:gap-8">
        <Link
          href={`/${lang}/docs`}
          className="text-secondary-foreground hover:text-foreground transition-colors"
        >
          {t.home.docs}
        </Link>
        <Link
          href="/blog/posts"
          className="text-secondary-foreground hover:text-foreground transition-colors"
        >
          {t.home.blog}
        </Link>
      </nav>
      {search && <div className="hidden w-40 sm:block lg:w-56">{search}</div>}
      <MobileNav
        links={[
          { href: `/${lang}/docs`, label: t.home.docs },
          { href: "/blog/posts", label: t.home.blog },
        ]}
        search={search}
      />
      <hr className="from-border/0 via-border/70 to-border/0 absolute top-16 right-0 left-0 z-10 m-0 h-px w-full border-none bg-linear-to-r" />
    </header>
  );
}
