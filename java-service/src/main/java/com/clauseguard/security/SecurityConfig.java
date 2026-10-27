package com.clauseguard.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.userdetails.User;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.provisioning.InMemoryUserDetailsManager;
import org.springframework.security.web.AuthenticationEntryPoint;
import org.springframework.security.web.SecurityFilterChain;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;

/**
 * 管理端鉴权。范围只覆盖 /api/v1/admin/**：用户端（提交者）目前没有身份概念，
 * 给它加登录会把「上传合同」这一条主路径变成需要注册的流程，属于另一个阶段的产品决策。
 *
 * 为什么用 BASIC 而不是会话/JWT：管理端只有两三个页面、调用方是运维自己，
 * BASIC + STATELESS 不需要登录页、不需要 Redis 会话、不需要签发与轮换密钥，
 * 是当前信息架构下改动面最小的真鉴权。等需要「谁在什么时候看过拓扑」的审计时再换 JWT。
 */
@Configuration
@EnableWebSecurity
@EnableConfigurationProperties(SecurityConfig.Accounts.class)
public class SecurityConfig {

    /**
     * 401 与 403 都手写 JSON：Spring 默认会 forward 到 /error，返回的是 HTML 错误页，
     * 而管理端是纯 fetch 调用，拿到 HTML 既解析不出来也看不出是认证问题还是服务异常。
     */
    @Bean
    SecurityFilterChain apiSecurity(HttpSecurity http, ObjectMapper json) throws Exception {
        AuthenticationEntryPoint unauthorized = (request, response, cause) -> {
            // 401 按 RFC 7235 必须带 WWW-Authenticate，否则客户端不知道用哪种方案重试。
            response.setHeader(HttpHeaders.WWW_AUTHENTICATE, "Basic realm=\"clauseguard\"");
            writeJson(response, json, 401, "UNAUTHORIZED", "需要管理员凭证");
        };

        http
                // 纯 JSON API + 无表单、无 Cookie 会话，CSRF 令牌没有附着点。
                .csrf(AbstractHttpConfigurer::disable)
                // 无会话：每个请求自带凭证，服务端不存登录态，扩容时不需要粘性会话。
                .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers("/api/v1/admin/**").hasRole("ADMIN")
                        // 用户端与探针暂时保持开放，是当前阶段的显式取舍，不是遗漏（README 已知限制 5）。
                        .requestMatchers("/api/v1/**", "/actuator/**", "/error").permitAll()
                        .anyRequest().permitAll())
                // 必须把 entry point 也交给 httpBasic：BasicAuthenticationFilter 用的是它自己那一个，
                // 「用户名密码错」走的是它而不是 exceptionHandling。不覆盖的话同一个接口会出现两种 401
                // 响应体（没带凭证是我们的 JSON，凭证错是 Spring 默认错误体 + realm="Realm"），
                // 前端就没法用同一段代码判断「是不是认证问题」。
                .httpBasic(basic -> basic.authenticationEntryPoint(unauthorized))
                // 默认的 X-Frame-Options: DENY 会连「同源 iframe」一起拒掉，而用户端左栏
                // 正是用同源 iframe 内嵌 PDF 原文——DENY 会让预览白屏。SAMEORIGIN 才是这个
                // 场景要的边界：别人的站点嵌不了，自己嵌自己可以。
                .headers(headers -> headers.frameOptions(frame -> frame.sameOrigin()))
                .exceptionHandling(ex -> ex
                        .authenticationEntryPoint(unauthorized)
                        .accessDeniedHandler((request, response, cause) ->
                                writeJson(response, json, 403, "FORBIDDEN", "当前账号没有管理员权限")));
        return http.build();
    }

    @Bean
    PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    /**
     * 内存用户：账号来源就这一个演示配置，不接数据库。
     * 启动时把 yml 里的明文用 BCrypt 编码后再放进内存——内存里不留明文，
     * 也避免把 hash 写死进配置后无法在本机验证（生产应换成外部用户源）。
     */
    @Bean
    UserDetailsService userDetailsService(Accounts accounts, PasswordEncoder encoder) {
        List<UserDetails> users = accounts.users().stream()
                .map(account -> User.withUsername(account.username())
                        .password(encoder.encode(account.password()))
                        .roles(account.role())
                        .build())
                .toList();
        return new InMemoryUserDetailsManager(users);
    }

    private static void writeJson(HttpServletResponse response, ObjectMapper json,
                                  int status, String code, String message) throws IOException {
        response.setStatus(status);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        json.writeValue(response.getWriter(), Map.of("code", code, "message", message));
    }

    /** 绑定 clauseguard.security.users，缺省为空列表，让没有配账号的环境仍能启动。 */
    @ConfigurationProperties("clauseguard.security")
    public record Accounts(List<Account> users) {

        public Accounts {
            users = users == null ? List.of() : List.copyOf(users);
        }

        public record Account(String username, String password, String role) {
        }
    }
}
