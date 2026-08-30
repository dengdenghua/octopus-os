/*
    SPDX-FileCopyrightText: 2010 Fredrik Höglund <fredrik@kde.org>
    SPDX-FileCopyrightText: 2011 Philipp Knechtges <philipp-dev@knechtges.com>
    SPDX-FileCopyrightText: 2018 Alex Nemeth <alex.nemeth329@gmail.com>

    SPDX-License-Identifier: GPL-2.0-or-later
*/

#include "echo_liquid_glass_effect.h"

#include "core/pixelgrid.h"
#include "core/rendertarget.h"
#include "core/renderviewport.h"
#include "effect/effecthandler.h"
#include "opengl/glplatform.h"
#include "scene/windowitem.h"

#include <QDBusConnection>
#include <QGuiApplication>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QLoggingCategory>
#include <QMatrix4x4>
#include <QPainterPath>
#include <QScreen>
#include <QTime>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <utility>

Q_LOGGING_CATEGORY(KWIN_ECHO_LIQUID_GLASS, "kwin_effect_echo_liquid_glass", QtWarningMsg)

static void ensureResources()
{
    Q_INIT_RESOURCE(echo_liquid_glass);
}

namespace KWin
{

constexpr qsizetype s_maxSurfaceCount = 8;
constexpr double s_maxCoordinate = 16384.0;
constexpr qint64 s_maxBlurBoundingArea = 7680LL * 4320LL;

static bool configureMaterial(EchoLiquidGlassSurface &surface, const QString &material)
{
    if (material == QStringLiteral("ultra-thin")) {
        surface.edgeWidth = 7.0;
        surface.refraction = 3.5;
        surface.materialResponse = 0.62;
        return true;
    }
    if (material == QStringLiteral("thin")) {
        surface.edgeWidth = 8.0;
        surface.refraction = 5.0;
        surface.materialResponse = 0.78;
        return true;
    }
    if (material == QStringLiteral("thick")) {
        surface.edgeWidth = 10.0;
        surface.refraction = 7.0;
        surface.materialResponse = 1.0;
        return true;
    }
    if (material == QStringLiteral("thick-dark")) {
        surface.edgeWidth = 10.0;
        surface.refraction = 6.0;
        surface.materialResponse = -0.92;
        return true;
    }
    if (material == QStringLiteral("ultra-thick")) {
        surface.edgeWidth = 12.0;
        surface.refraction = 8.5;
        surface.materialResponse = 1.15;
        return true;
    }
    return false;
}

EchoLiquidGlassEffect::EchoLiquidGlassEffect()
{
    ensureResources();

    m_downsamplePass.shader = ShaderManager::instance()->generateShaderFromFile(ShaderTrait::MapTexture,
                                                                                QStringLiteral(":/effects/echo-liquid-glass/shaders/vertex.vert"),
                                                                                QStringLiteral(":/effects/echo-liquid-glass/shaders/downsample.frag"));
    if (!m_downsamplePass.shader) {
        qCWarning(KWIN_ECHO_LIQUID_GLASS) << "Failed to load downsampling pass shader";
        return;
    } else {
        m_downsamplePass.mvpMatrixLocation = m_downsamplePass.shader->uniformLocation("modelViewProjectionMatrix");
        m_downsamplePass.offsetLocation = m_downsamplePass.shader->uniformLocation("offset");
        m_downsamplePass.halfpixelLocation = m_downsamplePass.shader->uniformLocation("halfpixel");
    }

    m_upsamplePass.shader = ShaderManager::instance()->generateShaderFromFile(ShaderTrait::MapTexture,
                                                                              QStringLiteral(":/effects/echo-liquid-glass/shaders/vertex.vert"),
                                                                              QStringLiteral(":/effects/echo-liquid-glass/shaders/upsample.frag"));
    if (!m_upsamplePass.shader) {
        qCWarning(KWIN_ECHO_LIQUID_GLASS) << "Failed to load upsampling pass shader";
        return;
    } else {
        m_upsamplePass.mvpMatrixLocation = m_upsamplePass.shader->uniformLocation("modelViewProjectionMatrix");
        m_upsamplePass.offsetLocation = m_upsamplePass.shader->uniformLocation("offset");
        m_upsamplePass.halfpixelLocation = m_upsamplePass.shader->uniformLocation("halfpixel");
    }

    m_materialPass.shader = ShaderManager::instance()->generateShaderFromFile(ShaderTrait::MapTexture,
                                                                              QStringLiteral(":/effects/echo-liquid-glass/shaders/vertex.vert"),
                                                                              QStringLiteral(":/effects/echo-liquid-glass/shaders/material.frag"));
    if (!m_materialPass.shader || !m_materialPass.shader->isValid()) {
        qCWarning(KWIN_ECHO_LIQUID_GLASS) << "Failed to load native Liquid Glass material shader";
        return;
    } else {
        m_materialPass.mvpMatrixLocation = m_materialPass.shader->uniformLocation("modelViewProjectionMatrix");
        m_materialPass.offsetLocation = m_materialPass.shader->uniformLocation("offset");
        m_materialPass.halfpixelLocation = m_materialPass.shader->uniformLocation("halfpixel");
        m_materialPass.outputSizeLocation = m_materialPass.shader->uniformLocation("outputSize");
        m_materialPass.surfaceCountLocation = m_materialPass.shader->uniformLocation("surfaceCount");
        m_materialPass.surfaceRectsLocation = m_materialPass.shader->uniformLocation("surfaceRects[0]");
        m_materialPass.surfaceParamsLocation = m_materialPass.shader->uniformLocation("surfaceParams[0]");
    }

    m_noisePass.shader = ShaderManager::instance()->generateShaderFromFile(ShaderTrait::MapTexture,
                                                                           QStringLiteral(":/effects/echo-liquid-glass/shaders/vertex.vert"),
                                                                           QStringLiteral(":/effects/echo-liquid-glass/shaders/noise.frag"));
    if (!m_noisePass.shader) {
        qCWarning(KWIN_ECHO_LIQUID_GLASS) << "Failed to load noise pass shader";
        return;
    } else {
        m_noisePass.mvpMatrixLocation = m_noisePass.shader->uniformLocation("modelViewProjectionMatrix");
        m_noisePass.noiseTextureSizeLocation = m_noisePass.shader->uniformLocation("noiseTextureSize");
        m_noisePass.texStartPosLocation = m_noisePass.shader->uniformLocation("texStartPos");
    }

    reconfigure(ReconfigureAll);

    connect(effects, &EffectsHandler::windowAdded, this, &EchoLiquidGlassEffect::slotWindowAdded);
    connect(effects, &EffectsHandler::windowDeleted, this, &EchoLiquidGlassEffect::slotWindowDeleted);
    connect(effects, &EffectsHandler::screenRemoved, this, &EchoLiquidGlassEffect::slotScreenRemoved);

    m_dbusRegistered = QDBusConnection::sessionBus().registerObject(
        QStringLiteral("/org/echoos/KWin/LiquidGlass"),
        this,
        QDBusConnection::ExportScriptableSlots);
    if (!m_dbusRegistered) {
        qCWarning(KWIN_ECHO_LIQUID_GLASS) << "Failed to register the Liquid Glass D-Bus object";
        return;
    }

    // Fetch the blur regions for all windows
    const auto stackingOrder = effects->stackingOrder();
    for (EffectWindow *window : stackingOrder) {
        slotWindowAdded(window);
    }

    m_valid = true;
}

EchoLiquidGlassEffect::~EchoLiquidGlassEffect()
{
    if (m_dbusRegistered) {
        QDBusConnection::sessionBus().unregisterObject(
            QStringLiteral("/org/echoos/KWin/LiquidGlass"));
    }
}

void EchoLiquidGlassEffect::reconfigure(ReconfigureFlags flags)
{
    Q_UNUSED(flags)
    m_iterationCount = 2;
    m_offset = 2;
    m_expandSize = 20;
    m_noiseStrength = 2;
    effects->addRepaintFull();
}

QRegion EchoLiquidGlassEffect::roundedSurfaceRegion(const QRectF &rect, qreal radius)
{
    QPainterPath path;
    const qreal boundedRadius = std::clamp(
        radius,
        0.0,
        std::min(rect.width(), rect.height()) / 2.0);
    path.addRoundedRect(rect, boundedRadius, boundedRadius, Qt::AbsoluteSize);
    return QRegion(path.toFillPolygon().toPolygon(), Qt::WindingFill);
}

bool EchoLiquidGlassEffect::isEchoShellWindow(const EffectWindow *w) const
{
    if (!w || w->isDesktop()) {
        return false;
    }
    if (w->caption() == QStringLiteral("Echo Liquid Glass Background")) {
        return false;
    }
    const QString identity = w->windowClass().toLower();
    return identity.contains(QStringLiteral("echo-os-desktop"))
        || identity.contains(QStringLiteral("echo-shell"));
}

void EchoLiquidGlassEffect::refreshEchoWindows()
{
    const auto stackingOrder = effects->stackingOrder();
    for (EffectWindow *window : stackingOrder) {
        updateBlurRegion(window);
    }
    effects->addRepaintFull();
}

bool EchoLiquidGlassEffect::SyncSurfaces(const QString &payload)
{
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(payload.toUtf8(), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        m_status = QStringLiteral("invalid-json");
        return false;
    }

    const QJsonObject root = document.object();
    const QJsonValue version = root.value(QStringLiteral("version"));
    const QJsonValue surfaceValue = root.value(QStringLiteral("surfaces"));
    const int contractVersion = version.toInt();
    if ((contractVersion != 1 && contractVersion != 2) || !surfaceValue.isArray()) {
        m_status = QStringLiteral("invalid-contract");
        return false;
    }

    const QJsonArray surfaces = surfaceValue.toArray();
    if (surfaces.size() > s_maxSurfaceCount) {
        m_status = QStringLiteral("too-many-surfaces");
        return false;
    }

    QRegion candidate;
    std::vector<EchoLiquidGlassSurface> candidateSurfaces;
    candidateSurfaces.reserve(surfaces.size());
    for (const QJsonValue &value : surfaces) {
        if (!value.isObject()) {
            m_status = QStringLiteral("invalid-surface");
            return false;
        }
        const QJsonObject surface = value.toObject();
        const double invalid = std::numeric_limits<double>::quiet_NaN();
        const double x = surface.value(QStringLiteral("x")).toDouble(invalid);
        const double y = surface.value(QStringLiteral("y")).toDouble(invalid);
        const double width = surface.value(QStringLiteral("width")).toDouble(invalid);
        const double height = surface.value(QStringLiteral("height")).toDouble(invalid);
        const double radius = surface.value(QStringLiteral("cornerRadius")).toDouble(invalid);
        if (!std::isfinite(x) || !std::isfinite(y)
            || !std::isfinite(width) || !std::isfinite(height)
            || !std::isfinite(radius)
            || x < 0.0 || y < 0.0 || width <= 1.0 || height <= 1.0
            || radius < 0.0 || x + width > s_maxCoordinate || y + height > s_maxCoordinate) {
            m_status = QStringLiteral("invalid-geometry");
            return false;
        }
        const double boundedRadius = std::min(
            radius,
            std::min(width, height) / 2.0);
        EchoLiquidGlassSurface descriptor{
            .rect = QRectF(x, y, width, height),
            .radius = boundedRadius,
        };
        const QJsonValue materialValue = surface.value(QStringLiteral("material"));
        const QString material = contractVersion == 1
            ? QStringLiteral("thick")
            : materialValue.toString();
        if ((contractVersion == 2 && !materialValue.isString())
            || !configureMaterial(descriptor, material)) {
            m_status = QStringLiteral("invalid-material");
            return false;
        }
        candidate += roundedSurfaceRegion(descriptor.rect, descriptor.radius);
        candidateSurfaces.push_back(descriptor);
    }

    const QRect bounds = candidate.boundingRect();
    const qint64 boundingArea = qint64(bounds.width()) * qint64(bounds.height());
    if (boundingArea > s_maxBlurBoundingArea) {
        m_status = QStringLiteral("region-too-large");
        return false;
    }

    m_echoRegion = candidate;
    m_echoSurfaces = std::move(candidateSurfaces);
    m_status = QStringLiteral("ready:%1:native-optics").arg(surfaces.size());
    refreshEchoWindows();
    return true;
}

void EchoLiquidGlassEffect::Clear()
{
    m_echoRegion = QRegion();
    m_echoSurfaces.clear();
    m_status = QStringLiteral("inactive");
    refreshEchoWindows();
}

QString EchoLiquidGlassEffect::Status() const
{
    return m_status;
}

void EchoLiquidGlassEffect::updateBlurRegion(EffectWindow *w)
{
    if (isEchoShellWindow(w) && !m_echoRegion.isEmpty()) {
        EchoLiquidGlassEffectData &data = m_windows[w];
        data.content = m_echoRegion;
        data.frame.reset();
        data.windowEffect = ItemEffect(w->windowItem());
        return;
    }

    if (auto it = m_windows.find(w); it != m_windows.end()) {
        effects->makeOpenGLContextCurrent();
        m_windows.erase(it);
    }
}

void EchoLiquidGlassEffect::slotWindowAdded(EffectWindow *w)
{
    updateBlurRegion(w);
}

void EchoLiquidGlassEffect::slotWindowDeleted(EffectWindow *w)
{
    if (auto it = m_windows.find(w); it != m_windows.end()) {
        effects->makeOpenGLContextCurrent();
        m_windows.erase(it);
    }
}

void EchoLiquidGlassEffect::slotScreenRemoved(KWin::Output *screen)
{
    for (auto &[window, data] : m_windows) {
        if (auto it = data.render.find(screen); it != data.render.end()) {
            effects->makeOpenGLContextCurrent();
            data.render.erase(it);
        }
    }
}

bool EchoLiquidGlassEffect::enabledByDefault()
{
    const auto context = effects->openglContext();
    if (!context || context->isSoftwareRenderer()) {
        return false;
    }
    GLPlatform *gl = context->glPlatform();

    if (gl->isIntel() && gl->chipClass() < SandyBridge) {
        return false;
    }
    if (gl->isPanfrost() && gl->chipClass() <= MaliT8XX) {
        return false;
    }
    // The blur effect works, but is painfully slow (FPS < 5) on Mali and VideoCore
    if (gl->isLima() || gl->isVideoCore4() || gl->isVideoCore3D()) {
        return false;
    }
    return true;
}

bool EchoLiquidGlassEffect::supported()
{
    return effects->openglContext() && (effects->openglContext()->supportsBlits() || effects->waylandDisplay());
}

QRegion EchoLiquidGlassEffect::blurRegion(EffectWindow *w) const
{
    QRegion region;

    if (auto it = m_windows.find(w); it != m_windows.end()) {
        const std::optional<QRegion> &content = it->second.content;
        const std::optional<QRegion> &frame = it->second.frame;
        if (content.has_value()) {
            if (content->isEmpty()) {
                // An empty region means that the blur effect should be enabled
                // for the whole window.
                region = w->contentsRect().toRect();
            } else {
                region = content->translated(w->contentsRect().topLeft().toPoint()) & w->contentsRect().toRect();
            }
            if (frame.has_value()) {
                region += frame.value();
            }
        } else if (frame.has_value()) {
            region = frame.value();
        }
    }

    return region;
}

void EchoLiquidGlassEffect::prePaintScreen(ScreenPrePaintData &data, std::chrono::milliseconds presentTime)
{
    m_paintedArea = QRegion();
    m_currentBlur = QRegion();
    m_currentScreen = effects->waylandDisplay() ? data.screen : nullptr;

    effects->prePaintScreen(data, presentTime);
}

void EchoLiquidGlassEffect::prePaintWindow(EffectWindow *w, WindowPrePaintData &data, std::chrono::milliseconds presentTime)
{
    // this effect relies on prePaintWindow being called in the bottom to top order

    effects->prePaintWindow(w, data, presentTime);

    const QRegion oldOpaque = data.opaque;
    if (data.opaque.intersects(m_currentBlur)) {
        // to blur an area partially we have to shrink the opaque area of a window
        QRegion newOpaque;
        for (const QRect &rect : data.opaque) {
            newOpaque += rect.adjusted(m_expandSize, m_expandSize, -m_expandSize, -m_expandSize);
        }
        data.opaque = newOpaque;

        // we don't have to blur a region we don't see
        m_currentBlur -= newOpaque;
    }

    // if we have to paint a non-opaque part of this window that intersects with the
    // currently blurred region we have to redraw the whole region
    if ((data.paint - oldOpaque).intersects(m_currentBlur)) {
        data.paint += m_currentBlur;
    }

    // in case this window has regions to be blurred
    const QRegion blurArea = blurRegion(w).boundingRect().translated(w->pos().toPoint());

    // if this window or a window underneath the blurred area is painted again we have to
    // blur everything
    if (m_paintedArea.intersects(blurArea) || data.paint.intersects(blurArea)) {
        data.paint += blurArea;
        // we have to check again whether we do not damage a blurred area
        // of a window
        if (blurArea.intersects(m_currentBlur)) {
            data.paint += m_currentBlur;
        }
    }

    m_currentBlur += blurArea;

    m_paintedArea -= data.opaque;
    m_paintedArea += data.paint;
}

bool EchoLiquidGlassEffect::shouldBlur(const EffectWindow *w, int mask, const WindowPaintData &data) const
{
    if (effects->activeFullScreenEffect() && !w->data(WindowForceBlurRole).toBool()) {
        return false;
    }

    if (w->isDesktop()) {
        return false;
    }

    bool scaled = !qFuzzyCompare(data.xScale(), 1.0) && !qFuzzyCompare(data.yScale(), 1.0);
    bool translated = data.xTranslation() || data.yTranslation();

    if ((scaled || (translated || (mask & PAINT_WINDOW_TRANSFORMED))) && !w->data(WindowForceBlurRole).toBool()) {
        return false;
    }

    return true;
}

void EchoLiquidGlassEffect::drawWindow(const RenderTarget &renderTarget, const RenderViewport &viewport, EffectWindow *w, int mask, const QRegion &region, WindowPaintData &data)
{
    blur(renderTarget, viewport, w, mask, region, data);

    // Draw the window over the blurred area
    effects->drawWindow(renderTarget, viewport, w, mask, region, data);
}

GLTexture *EchoLiquidGlassEffect::ensureNoiseTexture()
{
    if (m_noiseStrength == 0) {
        return nullptr;
    }

    const qreal scale = std::max(1.0, QGuiApplication::primaryScreen()->logicalDotsPerInch() / 96.0);
    if (!m_noisePass.noiseTexture || m_noisePass.noiseTextureScale != scale || m_noisePass.noiseTextureStength != m_noiseStrength) {
        // Init randomness based on time
        std::srand((uint)QTime::currentTime().msec());

        QImage noiseImage(QSize(256, 256), QImage::Format_Grayscale8);

        for (int y = 0; y < noiseImage.height(); y++) {
            uint8_t *noiseImageLine = (uint8_t *)noiseImage.scanLine(y);

            for (int x = 0; x < noiseImage.width(); x++) {
                noiseImageLine[x] = std::rand() % m_noiseStrength;
            }
        }

        noiseImage = noiseImage.scaled(noiseImage.size() * scale);

        m_noisePass.noiseTexture = GLTexture::upload(noiseImage);
        if (!m_noisePass.noiseTexture) {
            return nullptr;
        }
        m_noisePass.noiseTexture->setFilter(GL_NEAREST);
        m_noisePass.noiseTexture->setWrapMode(GL_REPEAT);
        m_noisePass.noiseTextureScale = scale;
        m_noisePass.noiseTextureStength = m_noiseStrength;
    }

    return m_noisePass.noiseTexture.get();
}

void EchoLiquidGlassEffect::blur(const RenderTarget &renderTarget, const RenderViewport &viewport, EffectWindow *w, int mask, const QRegion &region, WindowPaintData &data)
{
    auto it = m_windows.find(w);
    if (it == m_windows.end()) {
        return;
    }

    EchoLiquidGlassEffectData &blurInfo = it->second;
    EchoLiquidGlassRenderData &renderInfo = blurInfo.render[m_currentScreen];
    if (!shouldBlur(w, mask, data)) {
        return;
    }

    // Compute the effective blur shape. Note that if the window is transformed, so will be the blur shape.
    QRegion blurShape = blurRegion(w).translated(w->pos().toPoint());
    if (data.xScale() != 1 || data.yScale() != 1) {
        QPoint pt = blurShape.boundingRect().topLeft();
        QRegion scaledShape;
        for (const QRect &r : blurShape) {
            const QPointF topLeft(pt.x() + (r.x() - pt.x()) * data.xScale() + data.xTranslation(),
                                  pt.y() + (r.y() - pt.y()) * data.yScale() + data.yTranslation());
            const QPoint bottomRight(std::floor(topLeft.x() + r.width() * data.xScale()) - 1,
                                     std::floor(topLeft.y() + r.height() * data.yScale()) - 1);
            scaledShape += QRect(QPoint(std::floor(topLeft.x()), std::floor(topLeft.y())), bottomRight);
        }
        blurShape = scaledShape;
    } else if (data.xTranslation() || data.yTranslation()) {
        blurShape.translate(std::round(data.xTranslation()), std::round(data.yTranslation()));
    }

    const QRect backgroundRect = blurShape.boundingRect();
    const QRect deviceBackgroundRect = snapToPixelGrid(scaledRect(backgroundRect, viewport.scale()));
    const auto opacity = w->opacity() * data.opacity();

    // Get the effective shape that will be actually blurred. It's possible that all of it will be clipped.
    QList<QRectF> effectiveShape;
    effectiveShape.reserve(blurShape.rectCount());
    if (region != infiniteRegion()) {
        for (const QRect &clipRect : region) {
            const QRectF deviceClipRect = snapToPixelGridF(scaledRect(clipRect, viewport.scale()))
                                              .translated(-deviceBackgroundRect.topLeft());
            for (const QRect &shapeRect : blurShape) {
                const QRectF deviceShapeRect = snapToPixelGridF(scaledRect(shapeRect.translated(-backgroundRect.topLeft()), viewport.scale()));
                if (const QRectF intersected = deviceClipRect.intersected(deviceShapeRect); !intersected.isEmpty()) {
                    effectiveShape.append(intersected);
                }
            }
        }
    } else {
        for (const QRect &rect : blurShape) {
            effectiveShape.append(snapToPixelGridF(scaledRect(rect.translated(-backgroundRect.topLeft()), viewport.scale())));
        }
    }
    if (effectiveShape.isEmpty()) {
        return;
    }

    // Maybe reallocate offscreen render targets. Keep in mind that the first one contains
    // original background behind the window, it's not blurred.
    GLenum textureFormat = GL_RGBA8;
    if (renderTarget.texture()) {
        textureFormat = renderTarget.texture()->internalFormat();
    }

    if (renderInfo.framebuffers.size() != (m_iterationCount + 1) || renderInfo.textures[0]->size() != backgroundRect.size() || renderInfo.textures[0]->internalFormat() != textureFormat) {
        renderInfo.framebuffers.clear();
        renderInfo.textures.clear();

        glClearColor(0, 0, 0, 0);
        for (size_t i = 0; i <= m_iterationCount; ++i) {
            auto texture = GLTexture::allocate(textureFormat, backgroundRect.size() / (1 << i));
            if (!texture) {
                qCWarning(KWIN_ECHO_LIQUID_GLASS) << "Failed to allocate an offscreen texture";
                return;
            }
            texture->setFilter(GL_LINEAR);
            texture->setWrapMode(GL_CLAMP_TO_EDGE);

            auto framebuffer = std::make_unique<GLFramebuffer>(texture.get());
            if (!framebuffer->valid()) {
                qCWarning(KWIN_ECHO_LIQUID_GLASS) << "Failed to create an offscreen framebuffer";
                return;
            }
            OpenGlContext::currentContext()->pushFramebuffer(framebuffer.get());
            glClear(GL_COLOR_BUFFER_BIT);
            OpenGlContext::currentContext()->popFramebuffer();
            renderInfo.textures.push_back(std::move(texture));
            renderInfo.framebuffers.push_back(std::move(framebuffer));
        }
    }

    // Fetch the pixels behind the shape that is going to be blurred.
    const QRegion dirtyRegion = region & backgroundRect;
    for (const QRect &dirtyRect : dirtyRegion) {
        renderInfo.framebuffers[0]->blitFromRenderTarget(renderTarget, viewport, dirtyRect, dirtyRect.translated(-backgroundRect.topLeft()));
    }

    // Upload the geometry: the first 6 vertices are used when downsampling and upsampling offscreen,
    // the remaining vertices are used when rendering on the screen.
    GLVertexBuffer *vbo = GLVertexBuffer::streamingBuffer();
    vbo->reset();
    vbo->setAttribLayout(std::span(GLVertexBuffer::GLVertex2DLayout), sizeof(GLVertex2D));

    const int vertexCount = effectiveShape.size() * 6;
    if (auto result = vbo->map<GLVertex2D>(6 + vertexCount)) {
        auto map = *result;

        size_t vboIndex = 0;

        // The geometry that will be blurred offscreen, in logical pixels.
        {
            const QRectF localRect = QRectF(0, 0, backgroundRect.width(), backgroundRect.height());

            const float x0 = localRect.left();
            const float y0 = localRect.top();
            const float x1 = localRect.right();
            const float y1 = localRect.bottom();

            const float u0 = x0 / backgroundRect.width();
            const float v0 = 1.0f - y0 / backgroundRect.height();
            const float u1 = x1 / backgroundRect.width();
            const float v1 = 1.0f - y1 / backgroundRect.height();

            // first triangle
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x0, y0),
                .texcoord = QVector2D(u0, v0),
            };
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x1, y1),
                .texcoord = QVector2D(u1, v1),
            };
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x0, y1),
                .texcoord = QVector2D(u0, v1),
            };

            // second triangle
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x0, y0),
                .texcoord = QVector2D(u0, v0),
            };
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x1, y0),
                .texcoord = QVector2D(u1, v0),
            };
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x1, y1),
                .texcoord = QVector2D(u1, v1),
            };
        }

        // The geometry that will be painted on screen, in device pixels.
        for (const QRectF &rect : effectiveShape) {
            const float x0 = rect.left();
            const float y0 = rect.top();
            const float x1 = rect.right();
            const float y1 = rect.bottom();

            const float u0 = x0 / deviceBackgroundRect.width();
            const float v0 = 1.0f - y0 / deviceBackgroundRect.height();
            const float u1 = x1 / deviceBackgroundRect.width();
            const float v1 = 1.0f - y1 / deviceBackgroundRect.height();

            // first triangle
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x0, y0),
                .texcoord = QVector2D(u0, v0),
            };
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x1, y1),
                .texcoord = QVector2D(u1, v1),
            };
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x0, y1),
                .texcoord = QVector2D(u0, v1),
            };

            // second triangle
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x0, y0),
                .texcoord = QVector2D(u0, v0),
            };
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x1, y0),
                .texcoord = QVector2D(u1, v0),
            };
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x1, y1),
                .texcoord = QVector2D(u1, v1),
            };
        }

        vbo->unmap();
    } else {
        qCWarning(KWIN_ECHO_LIQUID_GLASS) << "Failed to map vertex buffer";
        return;
    }

    vbo->bindArrays();

    // The downsample pass of the dual Kawase algorithm: the background will be scaled down 50% every iteration.
    {
        ShaderManager::instance()->pushShader(m_downsamplePass.shader.get());

        QMatrix4x4 projectionMatrix;
        projectionMatrix.ortho(QRectF(0.0, 0.0, backgroundRect.width(), backgroundRect.height()));

        m_downsamplePass.shader->setUniform(m_downsamplePass.mvpMatrixLocation, projectionMatrix);
        m_downsamplePass.shader->setUniform(m_downsamplePass.offsetLocation, float(m_offset));

        for (size_t i = 1; i < renderInfo.framebuffers.size(); ++i) {
            const auto &read = renderInfo.framebuffers[i - 1];
            const auto &draw = renderInfo.framebuffers[i];

            const QVector2D halfpixel(0.5 / read->colorAttachment()->width(),
                                      0.5 / read->colorAttachment()->height());
            m_downsamplePass.shader->setUniform(m_downsamplePass.halfpixelLocation, halfpixel);

            read->colorAttachment()->bind();

            GLFramebuffer::pushFramebuffer(draw.get());
            vbo->draw(GL_TRIANGLES, 0, 6);
        }

        ShaderManager::instance()->popShader();
    }

    // Upsample the intermediate levels. The final screen pass is handled by the
    // material shader so blur, refraction, chroma and the Fresnel rim are one
    // compositor operation rather than stacked application-side contours.
    {
        ShaderManager::instance()->pushShader(m_upsamplePass.shader.get());

        QMatrix4x4 projectionMatrix;
        projectionMatrix.ortho(QRectF(0.0, 0.0, backgroundRect.width(), backgroundRect.height()));

        m_upsamplePass.shader->setUniform(m_upsamplePass.mvpMatrixLocation, projectionMatrix);
        m_upsamplePass.shader->setUniform(m_upsamplePass.offsetLocation, float(m_offset));

        for (size_t i = renderInfo.framebuffers.size() - 1; i > 1; --i) {
            GLFramebuffer::popFramebuffer();
            const auto &read = renderInfo.framebuffers[i];

            const QVector2D halfpixel(0.5 / read->colorAttachment()->width(),
                                      0.5 / read->colorAttachment()->height());
            m_upsamplePass.shader->setUniform(m_upsamplePass.halfpixelLocation, halfpixel);

            read->colorAttachment()->bind();

            vbo->draw(GL_TRIANGLES, 0, 6);
        }

        ShaderManager::instance()->popShader();
    }

    {
        // The last upsampling pass is rendered on the screen, not in framebuffers[0].
        GLFramebuffer::popFramebuffer();
        const auto &read = renderInfo.framebuffers[1];

        ShaderManager::instance()->pushShader(m_materialPass.shader.get());

        QMatrix4x4 projectionMatrix = viewport.projectionMatrix();
        projectionMatrix.translate(deviceBackgroundRect.x(), deviceBackgroundRect.y());
        m_materialPass.shader->setUniform(m_materialPass.mvpMatrixLocation, projectionMatrix);
        m_materialPass.shader->setUniform(m_materialPass.offsetLocation, float(m_offset));

        const QVector2D halfpixel(0.5 / read->colorAttachment()->width(),
                                  0.5 / read->colorAttachment()->height());
        m_materialPass.shader->setUniform(m_materialPass.halfpixelLocation, halfpixel);
        m_materialPass.shader->setUniform(
            m_materialPass.outputSizeLocation,
            QVector2D(deviceBackgroundRect.width(), deviceBackgroundRect.height()));

        std::array<GLfloat, 32> surfaceRects{};
        std::array<GLfloat, 32> surfaceParams{};
        int surfaceCount = 0;
        const QPointF contentOrigin = w->pos() + w->contentsRect().topLeft();
        for (const EchoLiquidGlassSurface &surface : m_echoSurfaces) {
            if (surfaceCount >= s_maxSurfaceCount) {
                break;
            }
            const QRectF deviceRect = scaledRect(
                                          surface.rect.translated(contentOrigin),
                                          viewport.scale())
                                          .translated(-deviceBackgroundRect.topLeft());
            if (deviceRect.isEmpty()) {
                continue;
            }

            const int base = surfaceCount * 4;
            surfaceRects[base] = deviceRect.x();
            surfaceRects[base + 1] = deviceBackgroundRect.height()
                - deviceRect.y() - deviceRect.height();
            surfaceRects[base + 2] = deviceRect.width();
            surfaceRects[base + 3] = deviceRect.height();
            surfaceParams[base] = surface.radius * viewport.scale();
            surfaceParams[base + 1] = surface.edgeWidth * viewport.scale();
            surfaceParams[base + 2] = surface.refraction * viewport.scale();
            surfaceParams[base + 3] = surface.materialResponse;
            surfaceCount += 1;
        }
        m_materialPass.shader->setUniform(m_materialPass.surfaceCountLocation, surfaceCount);
        glUniform4fv(m_materialPass.surfaceRectsLocation, surfaceCount, surfaceRects.data());
        glUniform4fv(m_materialPass.surfaceParamsLocation, surfaceCount, surfaceParams.data());

        read->colorAttachment()->bind();

        // Modulate the blurred texture with the window opacity if the window isn't opaque
        if (opacity < 1.0) {
            glEnable(GL_BLEND);
            float o = 1.0f - (opacity);
            o = 1.0f - o * o;
            glBlendColor(0, 0, 0, o);
            glBlendFunc(GL_CONSTANT_ALPHA, GL_ONE_MINUS_CONSTANT_ALPHA);
        }

        vbo->draw(GL_TRIANGLES, 6, vertexCount);

        if (opacity < 1.0) {
            glDisable(GL_BLEND);
        }

        ShaderManager::instance()->popShader();
    }

    if (m_noiseStrength > 0) {
        // Apply an additive noise onto the blurred image. The noise is useful to mask banding
        // artifacts, which often happens due to the smooth color transitions in the blurred image.

        glEnable(GL_BLEND);
        if (opacity < 1.0) {
            glBlendFunc(GL_CONSTANT_ALPHA, GL_ONE);
        } else {
            glBlendFunc(GL_ONE, GL_ONE);
        }

        if (GLTexture *noiseTexture = ensureNoiseTexture()) {
            ShaderManager::instance()->pushShader(m_noisePass.shader.get());

            QMatrix4x4 projectionMatrix = viewport.projectionMatrix();
            projectionMatrix.translate(deviceBackgroundRect.x(), deviceBackgroundRect.y());

            m_noisePass.shader->setUniform(m_noisePass.mvpMatrixLocation, projectionMatrix);
            m_noisePass.shader->setUniform(m_noisePass.noiseTextureSizeLocation, QVector2D(noiseTexture->width(), noiseTexture->height()));
            m_noisePass.shader->setUniform(m_noisePass.texStartPosLocation, QVector2D(deviceBackgroundRect.topLeft()));

            noiseTexture->bind();

            vbo->draw(GL_TRIANGLES, 6, vertexCount);

            ShaderManager::instance()->popShader();
        }

        glDisable(GL_BLEND);
    }

    vbo->unbindArrays();
}

bool EchoLiquidGlassEffect::isActive() const
{
    return m_valid && !m_echoRegion.isEmpty() && !effects->isScreenLocked();
}

bool EchoLiquidGlassEffect::blocksDirectScanout() const
{
    return isActive();
}

} // namespace KWin
