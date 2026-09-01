#version 140

uniform sampler2D texUnit;
uniform float offset;
uniform vec2 halfpixel;
uniform vec2 outputSize;
uniform int surfaceCount;
uniform vec4 surfaceRects[8];
uniform vec4 surfaceParams[8];

in vec2 uv;

out vec4 fragColor;

float roundedRectSdf(vec2 point, vec4 rect, float radius)
{
    vec2 halfSize = rect.zw * 0.5;
    vec2 centre = rect.xy + halfSize;
    vec2 q = abs(point - centre) - halfSize + radius;
    return min(max(q.x, q.y), 0.0) + length(max(q, 0.0)) - radius;
}

vec4 kawaseSample(vec2 sampleUv)
{
    vec4 sum = texture(texUnit, sampleUv + vec2(-halfpixel.x * 2.0, 0.0) * offset);
    sum += texture(texUnit, sampleUv + vec2(-halfpixel.x, halfpixel.y) * offset) * 2.0;
    sum += texture(texUnit, sampleUv + vec2(0.0, halfpixel.y * 2.0) * offset);
    sum += texture(texUnit, sampleUv + vec2(halfpixel.x, halfpixel.y) * offset) * 2.0;
    sum += texture(texUnit, sampleUv + vec2(halfpixel.x * 2.0, 0.0) * offset);
    sum += texture(texUnit, sampleUv + vec2(halfpixel.x, -halfpixel.y) * offset) * 2.0;
    sum += texture(texUnit, sampleUv + vec2(0.0, -halfpixel.y * 2.0) * offset);
    sum += texture(texUnit, sampleUv + vec2(-halfpixel.x, -halfpixel.y) * offset) * 2.0;
    return sum / 12.0;
}

void main(void)
{
    vec2 point = uv * outputSize;
    vec4 matchedRect = vec4(0.0);
    vec4 matchedParams = vec4(0.0);
    float distanceToEdge = 1.0;
    bool matched = false;

    for (int i = 0; i < 8; i += 1) {
        if (i >= surfaceCount) {
            break;
        }
        float candidate = roundedRectSdf(point, surfaceRects[i], surfaceParams[i].x);
        if (candidate <= 0.75) {
            matchedRect = surfaceRects[i];
            matchedParams = surfaceParams[i];
            distanceToEdge = candidate;
            matched = true;
            break;
        }
    }

    if (!matched) {
        fragColor = kawaseSample(uv);
        return;
    }

    float edgeWidth = max(1.0, matchedParams.y);
    float edge = 1.0 - smoothstep(0.0, edgeWidth, -distanceToEdge);
    float dx = roundedRectSdf(point + vec2(0.75, 0.0), matchedRect, matchedParams.x)
        - roundedRectSdf(point - vec2(0.75, 0.0), matchedRect, matchedParams.x);
    float dy = roundedRectSdf(point + vec2(0.0, 0.75), matchedRect, matchedParams.x)
        - roundedRectSdf(point - vec2(0.0, 0.75), matchedRect, matchedParams.x);
    vec2 normal = normalize(vec2(dx, dy) + vec2(0.00001));

    vec2 refraction = normal * edge * edge * matchedParams.z / max(outputSize, vec2(1.0));
    vec2 refractedUv = clamp(uv - refraction, vec2(0.001), vec2(0.999));
    vec4 base = kawaseSample(refractedUv);

    float materialResponse = matchedParams.w;
    float responseMagnitude = abs(materialResponse);
    vec2 chroma = normal * edge * (0.7 + 1.25 * responseMagnitude)
        / max(outputSize, vec2(1.0));
    float red = texture(texUnit, clamp(refractedUv + chroma, vec2(0.001), vec2(0.999))).r;
    float blue = texture(texUnit, clamp(refractedUv - chroma, vec2(0.001), vec2(0.999))).b;
    float chromaMix = edge * 0.52;
    vec3 colour = vec3(
        mix(base.r, red, chromaMix),
        base.g,
        mix(base.b, blue, chromaMix)
    );

    float luminance = dot(colour, vec3(0.2126, 0.7152, 0.0722));
    colour = mix(vec3(luminance), colour, 1.035 + responseMagnitude * 0.025);

    vec2 lightDirection = normalize(vec2(-0.58, 0.82));
    float lightFacing = max(dot(normal, lightDirection), 0.0);
    float darkFacing = max(dot(normal, -lightDirection), 0.0);
    float fresnel = edge * (0.34 + 0.66 * lightFacing * lightFacing);
    float highlight = fresnel * (0.045 + responseMagnitude * 0.055);
    colour = mix(colour, vec3(0.94, 0.98, 1.0), clamp(highlight, 0.0, 0.16));
    colour *= 1.0 - edge * darkFacing * 0.035;
    colour *= 1.0 - max(-materialResponse, 0.0) * 0.075;

    fragColor = vec4(colour, base.a);
}
