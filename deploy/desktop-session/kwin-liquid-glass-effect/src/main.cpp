/*
    SPDX-FileCopyrightText: 2026 Echo OS contributors

    SPDX-License-Identifier: GPL-2.0-or-later
*/

#include "echo_liquid_glass_effect.h"

namespace KWin
{

KWIN_EFFECT_FACTORY_SUPPORTED_ENABLED(EchoLiquidGlassEffect,
                                      "metadata.json",
                                      return EchoLiquidGlassEffect::supported();,
                                      return EchoLiquidGlassEffect::enabledByDefault();)

} // namespace KWin

#include "main.moc"
