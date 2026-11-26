"use client";

import {
  ArrowUpRightIcon,
  CarFrontIcon,
  LayersIcon,
  SettingsIcon,
  ShieldCheckIcon,
} from "lucide-react";
import Image from "next/image";

import { useI18n } from "@/core/i18n/hooks";

const actionIcons = [CarFrontIcon, ShieldCheckIcon, LayersIcon, SettingsIcon];

/** Presentation only; the host opens a picker and owns native submission. */
export function BusinessWelcome({
  disabled,
  onSelect,
}: {
  disabled: boolean;
  onSelect: (index: number, opener: HTMLButtonElement) => void;
}) {
  const { t } = useI18n();
  return (
    <section className="cvci-welcome" aria-labelledby="cvci-welcome-title">
      <div className="cvci-welcome-brand">
        <Image
          src="/cvci/emblem.png"
          width={104}
          height={104}
          alt=""
          unoptimized
        />
        <p>{t.workspaceBrand.name}</p>
      </div>
      <h1 id="cvci-welcome-title">{t.workspaceBrand.headline}</h1>
      <p className="cvci-welcome-description">{t.workspaceBrand.description}</p>
      <div className="cvci-welcome-divider">
        <span>{t.workspaceBrand.startQuestion}</span>
      </div>
      <div className="cvci-starter-grid">
        {t.workspaceBrand.starters.map((starter, index) => {
          const Icon = actionIcons[index] ?? LayersIcon;
          return (
            <button
              key={starter.title}
              type="button"
              className="cvci-starter-card"
              disabled={disabled}
              onClick={(event) => onSelect(index, event.currentTarget)}
              title={starter.hint}
            >
              <Icon className="cvci-starter-icon" aria-hidden="true" />
              <ArrowUpRightIcon
                className="cvci-starter-arrow"
                aria-hidden="true"
              />
              <span className="cvci-starter-title">{starter.title}</span>
              <span className="cvci-starter-description">
                {starter.description}
              </span>
              <span className="sr-only">{starter.hint}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
