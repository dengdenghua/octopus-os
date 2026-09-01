#include <napi.h>

#import <AppKit/AppKit.h>
#import <QuartzCore/QuartzCore.h>
#import <objc/message.h>
#import <objc/runtime.h>

#include <algorithm>
#include <string>
#include <unordered_set>
#include <vector>

constexpr std::size_t kMaximumSurfaces = 8;
static const void *kEchoLiquidGlassSceneKey = &kEchoLiquidGlassSceneKey;

@interface EchoLiquidGlassScene : NSObject
@property(nonatomic, weak) NSView *container;
@property(nonatomic, strong) NSView *wallpaper;
@property(nonatomic, strong) NSWindow *backgroundWindow;
@property(nonatomic, strong) NSMutableDictionary<NSString *, NSView *> *surfaces;
@property(nonatomic, copy) NSString *wallpaperPath;
@property(nonatomic, copy) NSString *materialName;
@property(nonatomic, strong) id moveObserver;
@property(nonatomic, strong) id resizeObserver;
@end

@implementation EchoLiquidGlassScene
@end

template <typename Callback>
void RunOnMain(Callback callback) {
  if ([NSThread isMainThread]) {
    callback();
  } else {
    dispatch_sync(dispatch_get_main_queue(), ^{
      callback();
    });
  }
}

NSView *ContainerFromHandle(const Napi::Value &value) {
  if (!value.IsBuffer()) return nil;
  auto handle = value.As<Napi::Buffer<unsigned char>>();
  if (handle.Length() < sizeof(NSView *)) return nil;
  auto pointer = reinterpret_cast<NSView * __unsafe_unretained *>(handle.Data());
  return *pointer;
}

double NumberProperty(const Napi::Object &object, const char *key,
                      double fallback = 0.0) {
  const Napi::Value value = object.Get(key);
  return value.IsNumber() ? value.As<Napi::Number>().DoubleValue() : fallback;
}

std::string StringProperty(const Napi::Object &object, const char *key) {
  const Napi::Value value = object.Get(key);
  return value.IsString() ? value.As<Napi::String>().Utf8Value() : "";
}

NSColor *TintForMaterial(NSString *material) {
  if ([material isEqualToString:@"ultra-thin"]) {
    return [NSColor colorWithSRGBRed:0.91 green:0.96 blue:1.0 alpha:0.07];
  }
  if ([material isEqualToString:@"thin"]) {
    return [NSColor colorWithSRGBRed:0.91 green:0.96 blue:1.0 alpha:0.12];
  }
  if ([material isEqualToString:@"thick-dark"]) {
    return [NSColor colorWithSRGBRed:0.14 green:0.19 blue:0.29 alpha:0.18];
  }
  if ([material isEqualToString:@"ultra-thick"]) {
    return [NSColor colorWithSRGBRed:0.91 green:0.95 blue:1.0 alpha:0.2];
  }
  return [NSColor colorWithSRGBRed:0.9 green:0.95 blue:1.0 alpha:0.15];
}

NSView *CreateGlassSurface(NSString **materialName) {
  Class glassClass = NSClassFromString(@"NSGlassEffectView");
  if (glassClass) {
    *materialName = @"NSGlassEffectView";
    return [[glassClass alloc] initWithFrame:NSZeroRect];
  }

  *materialName = @"NSVisualEffectView";
  NSVisualEffectView *fallback = [[NSVisualEffectView alloc] initWithFrame:NSZeroRect];
  fallback.blendingMode = NSVisualEffectBlendingModeWithinWindow;
  fallback.material = NSVisualEffectMaterialHUDWindow;
  fallback.state = NSVisualEffectStateActive;
  return fallback;
}

void ConfigureGlassSurface(NSView *surface, NSRect frame, CGFloat cornerRadius,
                           NSString *material) {
  surface.frame = frame;
  surface.hidden = NO;
  surface.wantsLayer = YES;
  surface.layer.cornerRadius = cornerRadius;
  surface.layer.cornerCurve = kCACornerCurveContinuous;
  surface.layer.masksToBounds = YES;

  NSColor *tint = TintForMaterial(material);
  SEL tintSelector = NSSelectorFromString(@"setTintColor:");
  if ([surface respondsToSelector:tintSelector]) {
    ((void (*)(id, SEL, id))objc_msgSend)(surface, tintSelector, tint);
  } else if (![surface isKindOfClass:[NSVisualEffectView class]]) {
    surface.layer.backgroundColor = tint.CGColor;
  }
}

EchoLiquidGlassScene *SceneForContainer(NSView *container) {
  return objc_getAssociatedObject(container, kEchoLiquidGlassSceneKey);
}

void RemoveScene(NSView *container) {
  EchoLiquidGlassScene *scene = SceneForContainer(container);
  if (!scene) return;
  for (NSView *surface in [scene.surfaces allValues]) {
    [surface removeFromSuperview];
  }
  NSNotificationCenter *notifications = NSNotificationCenter.defaultCenter;
  if (scene.moveObserver) [notifications removeObserver:scene.moveObserver];
  if (scene.resizeObserver) [notifications removeObserver:scene.resizeObserver];
  NSWindow *hostWindow = container.window;
  if (scene.backgroundWindow) {
    [hostWindow removeChildWindow:scene.backgroundWindow];
    [scene.backgroundWindow orderOut:nil];
    [scene.backgroundWindow close];
  }
  objc_setAssociatedObject(container, kEchoLiquidGlassSceneKey, nil,
                           OBJC_ASSOCIATION_ASSIGN);
}

Napi::Value HasLiquidGlass(const Napi::CallbackInfo &info) {
  return Napi::Boolean::New(info.Env(), NSClassFromString(@"NSGlassEffectView") != nil);
}

Napi::Value InstallScene(const Napi::CallbackInfo &info) {
  Napi::Env env = info.Env();
  if (info.Length() < 2 || !info[0].IsBuffer() || !info[1].IsString()) {
    Napi::TypeError::New(env, "Expected (nativeWindowHandle: Buffer, wallpaperPath: string)")
        .ThrowAsJavaScriptException();
    return env.Null();
  }

  NSView *container = ContainerFromHandle(info[0]);
  const std::string wallpaperPath = info[1].As<Napi::String>().Utf8Value();
  if (!container || wallpaperPath.empty()) {
    return Napi::Object::New(env);
  }

  bool ok = false;
  NSString *materialName = @"unavailable";
  NSString *containerClassName = @"unavailable";
  NSString *contentClassName = @"unavailable";
  NSRect containerBounds = NSZeroRect;
  NSRect wallpaperFrame = NSZeroRect;
  NSInteger subviewCount = 0;
  NSInteger childWindowCount = 0;
  bool backgroundVisible = false;
  bool wallpaperHasContents = false;
  RunOnMain([&] {
    NSString *path = [NSString stringWithUTF8String:wallpaperPath.c_str()];
    NSImage *image = [[NSImage alloc] initWithContentsOfFile:path];
    if (!image) return;

    EchoLiquidGlassScene *scene = SceneForContainer(container);
    if (!scene) {
      NSWindow *hostWindow = container.window;
      if (!hostWindow) return;
      scene = [[EchoLiquidGlassScene alloc] init];
      scene.container = container;
      scene.surfaces = [NSMutableDictionary dictionary];
      scene.backgroundWindow = [[NSWindow alloc]
          initWithContentRect:hostWindow.frame
                    styleMask:NSWindowStyleMaskBorderless
                      backing:NSBackingStoreBuffered
                        defer:NO];
      scene.backgroundWindow.opaque = YES;
      scene.backgroundWindow.backgroundColor = NSColor.blackColor;
      scene.backgroundWindow.hasShadow = NO;
      scene.backgroundWindow.ignoresMouseEvents = YES;
      scene.backgroundWindow.level = hostWindow.level;
      scene.backgroundWindow.collectionBehavior =
          hostWindow.collectionBehavior |
          NSWindowCollectionBehaviorFullScreenAuxiliary |
          NSWindowCollectionBehaviorCanJoinAllSpaces;
      scene.backgroundWindow.releasedWhenClosed = NO;

      scene.wallpaper = [[NSView alloc]
          initWithFrame:NSMakeRect(0, 0, NSWidth(hostWindow.frame),
                                   NSHeight(hostWindow.frame))];
      scene.wallpaper.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
      scene.wallpaper.wantsLayer = YES;
      scene.wallpaper.layer.backgroundColor = NSColor.blackColor.CGColor;
      scene.wallpaper.layer.contentsGravity = kCAGravityResizeAspectFill;
      scene.wallpaper.layer.masksToBounds = YES;
      scene.backgroundWindow.contentView = scene.wallpaper;
      [hostWindow addChildWindow:scene.backgroundWindow ordered:NSWindowBelow];

      __weak EchoLiquidGlassScene *weakScene = scene;
      void (^syncBackgroundFrame)(NSNotification *) = ^(NSNotification *) {
        EchoLiquidGlassScene *strongScene = weakScene;
        NSWindow *strongHost = strongScene.container.window;
        if (!strongScene || !strongHost) return;
        [strongScene.backgroundWindow setFrame:strongHost.frame display:YES];
      };
      NSNotificationCenter *notifications = NSNotificationCenter.defaultCenter;
      scene.moveObserver = [notifications
          addObserverForName:NSWindowDidMoveNotification
                      object:hostWindow
                       queue:NSOperationQueue.mainQueue
                  usingBlock:syncBackgroundFrame];
      scene.resizeObserver = [notifications
          addObserverForName:NSWindowDidResizeNotification
                      object:hostWindow
                       queue:NSOperationQueue.mainQueue
                  usingBlock:syncBackgroundFrame];
      objc_setAssociatedObject(container, kEchoLiquidGlassSceneKey, scene,
                               OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    }

    NSRect proposedRect = NSMakeRect(0, 0, image.size.width, image.size.height);
    CGImageRef cgImage = [image CGImageForProposedRect:&proposedRect
                                               context:nil
                                                 hints:nil];
    if (!cgImage) return;
    scene.wallpaper.layer.contents = (__bridge id)cgImage;
    scene.wallpaper.layer.contentsScale =
        std::max<CGFloat>(1.0, container.window.backingScaleFactor);
    scene.wallpaperPath = path;
    [scene.backgroundWindow setFrame:container.window.frame display:YES];
    [container.window addChildWindow:scene.backgroundWindow ordered:NSWindowBelow];

    NSWindow *window = container.window;
    window.opaque = NO;
    window.backgroundColor = NSColor.clearColor;

    materialName = NSClassFromString(@"NSGlassEffectView")
                       ? @"NSGlassEffectView"
                       : @"NSVisualEffectView";
    scene.materialName = materialName;
    containerClassName = NSStringFromClass(container.class);
    contentClassName = window.contentView
                           ? NSStringFromClass(window.contentView.class)
                           : @"none";
    containerBounds = container.bounds;
    wallpaperFrame = scene.backgroundWindow.frame;
    subviewCount = container.subviews.count;
    childWindowCount = window.childWindows.count;
    backgroundVisible = scene.backgroundWindow.visible;
    wallpaperHasContents = scene.wallpaper.layer.contents != nil;
    ok = true;
  });

  Napi::Object result = Napi::Object::New(env);
  result.Set("ok", Napi::Boolean::New(env, ok));
  result.Set("material", Napi::String::New(env, materialName.UTF8String));
  result.Set("containerClass",
             Napi::String::New(env, containerClassName.UTF8String));
  result.Set("contentClass",
             Napi::String::New(env, contentClassName.UTF8String));
  result.Set("containerWidth", Napi::Number::New(env, containerBounds.size.width));
  result.Set("containerHeight", Napi::Number::New(env, containerBounds.size.height));
  result.Set("wallpaperWidth", Napi::Number::New(env, wallpaperFrame.size.width));
  result.Set("wallpaperHeight", Napi::Number::New(env, wallpaperFrame.size.height));
  result.Set("subviewCount", Napi::Number::New(env, subviewCount));
  result.Set("childWindowCount", Napi::Number::New(env, childWindowCount));
  result.Set("backgroundVisible", Napi::Boolean::New(env, backgroundVisible));
  result.Set("wallpaperHasContents",
             Napi::Boolean::New(env, wallpaperHasContents));
  return result;
}

Napi::Value UpdateSurfaces(const Napi::CallbackInfo &info) {
  Napi::Env env = info.Env();
  if (info.Length() < 2 || !info[0].IsBuffer() || !info[1].IsArray()) {
    Napi::TypeError::New(env, "Expected (nativeWindowHandle: Buffer, surfaces: array)")
        .ThrowAsJavaScriptException();
    return env.Null();
  }

  NSView *container = ContainerFromHandle(info[0]);
  Napi::Array input = info[1].As<Napi::Array>();
  if (!container) return Napi::Number::New(env, 0);

  struct SurfaceInput {
    std::string id;
    double x;
    double y;
    double width;
    double height;
    double cornerRadius;
    std::string material;
  };
  std::vector<SurfaceInput> surfaces;
  const std::size_t count = std::min<std::size_t>(input.Length(), kMaximumSurfaces);
  surfaces.reserve(count);
  for (std::size_t index = 0; index < count; ++index) {
    Napi::Value value = input.Get(static_cast<uint32_t>(index));
    if (!value.IsObject()) continue;
    Napi::Object object = value.As<Napi::Object>();
    SurfaceInput surface{
        StringProperty(object, "id"),
        NumberProperty(object, "x"),
        NumberProperty(object, "y"),
        NumberProperty(object, "width"),
        NumberProperty(object, "height"),
        NumberProperty(object, "cornerRadius", 18.0),
        StringProperty(object, "material")};
    if (!surface.id.empty() && surface.width > 0 && surface.height > 0) {
      surfaces.push_back(std::move(surface));
    }
  }

  std::size_t updated = 0;
  RunOnMain([&] {
    EchoLiquidGlassScene *scene = SceneForContainer(container);
    if (!scene) return;

    std::unordered_set<std::string> activeIds;
    for (const SurfaceInput &inputSurface : surfaces) {
      activeIds.insert(inputSurface.id);
      NSString *identifier = [NSString stringWithUTF8String:inputSurface.id.c_str()];
      NSView *surface = scene.surfaces[identifier];
      if (!surface) {
        NSString *materialName = nil;
        surface = CreateGlassSurface(&materialName);
        if (!surface) continue;
        scene.materialName = materialName;
        scene.surfaces[identifier] = surface;
        [container addSubview:surface
                   positioned:NSWindowBelow
                   relativeTo:nil];
      }

      const CGFloat nativeY = container.isFlipped
                                  ? inputSurface.y
                                  : NSHeight(container.bounds) - inputSurface.y -
                                        inputSurface.height;
      const NSRect frame = NSMakeRect(inputSurface.x, nativeY,
                                      inputSurface.width, inputSurface.height);
      ConfigureGlassSurface(
          surface, frame,
          std::min(inputSurface.cornerRadius,
                   std::min(inputSurface.width, inputSurface.height) / 2.0),
          [NSString stringWithUTF8String:inputSurface.material.c_str()]);
      updated += 1;
    }

    for (NSString *identifier in [scene.surfaces.allKeys copy]) {
      if (activeIds.count(identifier.UTF8String) == 0) {
        [scene.surfaces[identifier] removeFromSuperview];
        [scene.surfaces removeObjectForKey:identifier];
      }
    }
  });

  return Napi::Number::New(env, updated);
}

Napi::Value RemoveSceneBinding(const Napi::CallbackInfo &info) {
  Napi::Env env = info.Env();
  if (info.Length() < 1 || !info[0].IsBuffer()) {
    Napi::TypeError::New(env, "Expected nativeWindowHandle Buffer")
        .ThrowAsJavaScriptException();
    return env.Null();
  }
  NSView *container = ContainerFromHandle(info[0]);
  if (container) RunOnMain([&] { RemoveScene(container); });
  return env.Undefined();
}

Napi::Object Init(Napi::Env env, Napi::Object exports) {
  exports.Set("hasLiquidGlass", Napi::Function::New(env, HasLiquidGlass));
  exports.Set("installScene", Napi::Function::New(env, InstallScene));
  exports.Set("updateSurfaces", Napi::Function::New(env, UpdateSurfaces));
  exports.Set("removeScene", Napi::Function::New(env, RemoveSceneBinding));
  return exports;
}

NODE_API_MODULE(echo_liquid_glass, Init)
