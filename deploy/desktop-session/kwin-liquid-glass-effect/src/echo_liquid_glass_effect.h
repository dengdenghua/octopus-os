/*
    SPDX-FileCopyrightText: 2010 Fredrik Höglund <fredrik@kde.org>
    SPDX-FileCopyrightText: 2018 Alex Nemeth <alex.nemeth329@gmail.com>

    SPDX-License-Identifier: GPL-2.0-or-later
*/

#pragma once

#include "effect/effect.h"
#include "opengl/glutils.h"
#include "scene/item.h"

#include <QRegion>
#include <QRectF>
#include <QString>

#include <unordered_map>
#include <vector>

namespace KWin
{

struct EchoLiquidGlassRenderData
{
    /// Temporary render targets needed for the Dual Kawase algorithm, the first texture
    /// contains not blurred background behind the window, it's cached.
    std::vector<std::unique_ptr<GLTexture>> textures;
    std::vector<std::unique_ptr<GLFramebuffer>> framebuffers;
};

struct EchoLiquidGlassEffectData
{
    /// The region that should be blurred behind the window
    std::optional<QRegion> content;

    /// The region that should be blurred behind the frame
    std::optional<QRegion> frame;

    /// The render data per screen. Screens can have different color spaces.
    std::unordered_map<Output *, EchoLiquidGlassRenderData> render;

    ItemEffect windowEffect;
};

struct EchoLiquidGlassSurface
{
    QRectF rect;
    qreal radius = 0.0;
    qreal edgeWidth = 10.0;
    qreal refraction = 7.0;
    qreal materialResponse = 1.0;
};

class EchoLiquidGlassEffect : public KWin::Effect
{
    Q_OBJECT
    Q_CLASSINFO("D-Bus Interface", "org.echoos.KWin.LiquidGlass1")

public:
    EchoLiquidGlassEffect();
    ~EchoLiquidGlassEffect() override;

    static bool supported();
    static bool enabledByDefault();

    void reconfigure(ReconfigureFlags flags) override;
    void prePaintScreen(ScreenPrePaintData &data, std::chrono::milliseconds presentTime) override;
    void prePaintWindow(EffectWindow *w, WindowPrePaintData &data, std::chrono::milliseconds presentTime) override;
    void drawWindow(const RenderTarget &renderTarget, const RenderViewport &viewport, EffectWindow *w, int mask, const QRegion &region, WindowPaintData &data) override;

    bool provides(Feature feature) override;
    bool isActive() const override;

    int requestedEffectChainPosition() const override
    {
        return 20;
    }

    bool blocksDirectScanout() const override;

public Q_SLOTS:
    Q_SCRIPTABLE bool SyncSurfaces(const QString &payload);
    Q_SCRIPTABLE void Clear();
    Q_SCRIPTABLE QString Status() const;
    void slotWindowAdded(KWin::EffectWindow *w);
    void slotWindowDeleted(KWin::EffectWindow *w);
    void slotScreenRemoved(KWin::Output *screen);

private:
    QRegion blurRegion(EffectWindow *w) const;
    bool shouldBlur(const EffectWindow *w, int mask, const WindowPaintData &data) const;
    void updateBlurRegion(EffectWindow *w);
    void refreshEchoWindows();
    bool isEchoShellWindow(const EffectWindow *w) const;
    static QRegion roundedSurfaceRegion(const QRectF &rect, qreal radius);
    void blur(const RenderTarget &renderTarget, const RenderViewport &viewport, EffectWindow *w, int mask, const QRegion &region, WindowPaintData &data);
    GLTexture *ensureNoiseTexture();

private:
    struct
    {
        std::unique_ptr<GLShader> shader;
        int mvpMatrixLocation;
        int offsetLocation;
        int halfpixelLocation;
    } m_downsamplePass;

    struct
    {
        std::unique_ptr<GLShader> shader;
        int mvpMatrixLocation;
        int offsetLocation;
        int halfpixelLocation;
    } m_upsamplePass;

    struct
    {
        std::unique_ptr<GLShader> shader;
        int mvpMatrixLocation;
        int offsetLocation;
        int halfpixelLocation;
        int outputSizeLocation;
        int surfaceCountLocation;
        int surfaceRectsLocation;
        int surfaceParamsLocation;
    } m_materialPass;

    struct
    {
        std::unique_ptr<GLShader> shader;
        int mvpMatrixLocation;
        int noiseTextureSizeLocation;
        int texStartPosLocation;

        std::unique_ptr<GLTexture> noiseTexture;
        qreal noiseTextureScale = 1.0;
        int noiseTextureStength = 0;
    } m_noisePass;

    bool m_valid = false;
    bool m_dbusRegistered = false;
    QRegion m_echoRegion;
    std::vector<EchoLiquidGlassSurface> m_echoSurfaces;
    QString m_status = QStringLiteral("inactive");
    QRegion m_paintedArea; // keeps track of all painted areas (from bottom to top)
    QRegion m_currentBlur; // keeps track of the currently blured area of the windows(from bottom to top)
    Output *m_currentScreen = nullptr;

    size_t m_iterationCount = 2;
    int m_offset = 2;
    int m_expandSize = 20;
    int m_noiseStrength = 2;

    std::unordered_map<EffectWindow *, EchoLiquidGlassEffectData> m_windows;
};

inline bool EchoLiquidGlassEffect::provides(Effect::Feature feature)
{
    if (feature == Blur) {
        return true;
    }
    return KWin::Effect::provides(feature);
}

} // namespace KWin
